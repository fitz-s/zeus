# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: live alpha capture directive 2026-05-01 — replace hardcoded
#                  POLYMARKET_USER_WS_CONDITION_IDS with auto-derivation from
#                  the canonical market scanner, so the daemon subscribes only
#                  to markets it can trade and the subscription list cannot
#                  drift from on-chain truth as markets rotate.
#                  2026-05-01 live-blocker fixes: proxy bypass + adapter-sourced creds
"""Antibody tests for user-channel WS condition_ids auto-derive (2026-05-01).

Pins seven cross-module invariants:

  1. AUTO_DERIVE=1 with empty POLYMARKET_USER_WS_CONDITION_IDS pulls condition_ids
     from ``src.data.market_scanner.find_weather_markets``.
  2. POLYMARKET_USER_WS_CONDITION_IDS, when non-empty, ALWAYS wins — auto-derive
     never overrides an operator-pinned list.
  3. Duplicate condition_ids returned by the scanner are deduped before the
     ingestor is constructed (WS server rejects duplicate subscriptions).
  4. Auto-derive returning 0 condition_ids logs a WARNING + records a
     ``condition_ids_missing`` WS gap, and does NOT raise — the daemon stays
     in reduce_only=True mode rather than crashing on cold boot.
  5. ``ZEUS_USER_CHANNEL_WS_AUTO_DERIVE=1`` alone does NOT enable WS;
     ``ZEUS_USER_CHANNEL_WS_ENABLED=1`` is still required as the master toggle.
  6. (2026-05-01 live-blocker) _default_websocket_connect passes proxy=None to
     websockets.connect() — HTTPS_PROXY env var must NOT route WS traffic through
     the local proxy (which killed the connection in <2s before this fix).
  7. (2026-05-01 live-blocker) The live daemon sources WSAuth from the adapter's
     SDK creds (create_or_derive_api_key), NOT from POLYMARKET_API_KEY env var.
     The plist key was stale; derived creds are always the canonical source.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from src import main as zeus_main
from src.control import ws_gap_guard


@pytest.fixture(autouse=True)
def _clean_user_channel_state(monkeypatch):
    """Reset all WS-related env vars + module globals around every test."""
    for name in (
        "ZEUS_USER_CHANNEL_WS_ENABLED",
        "ZEUS_USER_CHANNEL_WS_AUTO_DERIVE",
        "POLYMARKET_USER_WS_CONDITION_IDS",
    ):
        monkeypatch.delenv(name, raising=False)
    # Reset the WS gap guard so previous tests don't leave a "condition_ids_missing"
    # gap on the table that would conflict with this test's assertions.
    ws_gap_guard.clear_for_test()
    # Module globals from previous tests in the same session.
    monkeypatch.setattr(zeus_main, "_user_channel_thread", None, raising=False)
    monkeypatch.setattr(zeus_main, "_user_channel_ingestor", None, raising=False)
    yield
    ws_gap_guard.clear_for_test()


class _FakeCreds:
    """Minimal stub for SDK L2 creds returned by adapter._sdk_client().creds."""

    api_key = "stub-derived-key"
    api_secret = "stub-derived-secret"
    api_passphrase = "stub-derived-passphrase"


class _FakeSdkClient:
    creds = _FakeCreds()


class _FakeAdapter:
    """Stub adapter — provides _sdk_client() so the daemon creds path succeeds."""

    def _sdk_client(self) -> _FakeSdkClient:
        return _FakeSdkClient()


class _FakeIngestor:
    instances: list["_FakeIngestor"] = []

    def __init__(self, adapter: Any, condition_ids: list[str], **kwargs: Any) -> None:
        self.adapter = adapter
        self.condition_ids = list(condition_ids)
        self.auth = kwargs.get("auth")
        type(self).instances.append(self)

    @classmethod
    def from_env(cls, adapter: Any, condition_ids: list[str]) -> "_FakeIngestor":
        return cls(adapter, condition_ids)

    async def start(self) -> None:  # pragma: no cover — never awaited in tests
        return None


@pytest.fixture
def _stub_ingestor(monkeypatch):
    """Stub PolymarketClient + PolymarketUserChannelIngestor + thread start."""
    _FakeIngestor.instances.clear()

    # Stub the polymarket client adapter.
    class _StubClient:
        def _ensure_v2_adapter(self) -> _FakeAdapter:
            return _FakeAdapter()

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _StubClient)
    monkeypatch.setattr(
        "src.ingest.polymarket_user_channel.PolymarketUserChannelIngestor",
        _FakeIngestor,
    )

    # Don't actually start a daemon thread — just record that one was constructed.
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

    return started


def test_auto_derive_populates_condition_ids(monkeypatch, _stub_ingestor):
    """Invariant #1: empty env var + AUTO_DERIVE=1 → scanner-derived list."""
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_AUTO_DERIVE", "1")
    monkeypatch.delenv("POLYMARKET_USER_WS_CONDITION_IDS", raising=False)

    monkeypatch.setattr(
        "src.data.market_scanner.find_weather_markets",
        lambda **kw: [
            {"condition_ids": ["0xaaa", "0xbbb"]},
            {"condition_ids": ["0xccc"]},
        ],
    )

    zeus_main._start_user_channel_ingestor_if_enabled()

    assert _FakeIngestor.instances, "ingestor must be constructed when auto-derive yields ids"
    assert _FakeIngestor.instances[-1].condition_ids == ["0xaaa", "0xbbb", "0xccc"]
    assert _stub_ingestor["count"] == 1


def test_env_var_override_wins(monkeypatch, _stub_ingestor):
    """Invariant #2: non-empty env var beats auto-derive."""
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_AUTO_DERIVE", "1")
    monkeypatch.setenv(
        "POLYMARKET_USER_WS_CONDITION_IDS",
        "0xpinned1, 0xpinned2",
    )

    # Scanner would return a different set; assert that we did NOT call it.
    sentinel: dict[str, int] = {"calls": 0}

    def _scanner_called(**_kw):
        sentinel["calls"] += 1
        return [{"condition_ids": ["0xshouldnotappear"]}]

    monkeypatch.setattr(
        "src.data.market_scanner.find_weather_markets", _scanner_called
    )

    zeus_main._start_user_channel_ingestor_if_enabled()

    assert _FakeIngestor.instances, "ingestor must be constructed when env var is set"
    assert _FakeIngestor.instances[-1].condition_ids == ["0xpinned1", "0xpinned2"]
    assert sentinel["calls"] == 0, "env-var path must not invoke the scanner"


def test_auto_derive_dedupe(monkeypatch, _stub_ingestor):
    """Invariant #3: scanner duplicates are deduped before subscription."""
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_AUTO_DERIVE", "1")

    monkeypatch.setattr(
        "src.data.market_scanner.find_weather_markets",
        lambda **kw: [
            {"condition_ids": ["0xdup", "0xunique"]},
            {"condition_ids": ["0xdup"]},  # duplicate across events
            {"condition_ids": ["0xunique", "0xanother"]},  # duplicate within event
        ],
    )

    zeus_main._start_user_channel_ingestor_if_enabled()

    assert _FakeIngestor.instances, "ingestor must be constructed"
    derived = _FakeIngestor.instances[-1].condition_ids
    assert derived == ["0xdup", "0xunique", "0xanother"], (
        "dedupe must be order-preserving and remove all duplicates"
    )
    assert len(derived) == len(set(derived))


def test_auto_derive_empty_logs_warning_no_error(monkeypatch, caplog):
    """Invariant #4: scanner returning [] is a WARNING, not an exception."""
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_AUTO_DERIVE", "1")
    monkeypatch.setattr(
        "src.data.market_scanner.find_weather_markets", lambda **kw: []
    )

    caplog.set_level(logging.WARNING, logger="zeus")
    # Must not raise; daemon must stay reduce_only.
    zeus_main._start_user_channel_ingestor_if_enabled()

    warning_messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "auto-derive yielded 0 condition_ids" in msg for msg in warning_messages
    ), f"expected auto-derive empty WARNING; saw {warning_messages!r}"

    gap_summary = ws_gap_guard.summary()
    assert gap_summary.get("gap_reason") == "condition_ids_missing", (
        f"WS gap guard must record condition_ids_missing; saw {gap_summary!r}"
    )


def test_auto_derive_includes_day0_markets(monkeypatch, _stub_ingestor):
    """Invariant #6 (PR #34 codex P1): auto-derive must include day0 markets.

    The scanner default ``min_hours_to_resolution=6.0`` excludes markets in
    the DAY0_CAPTURE window (<6h to settlement). If the auto-derive call
    inherits that default, the WS subscription set silently omits day0
    condition_ids — Zeus would actively trade via ``DiscoveryMode.DAY0_CAPTURE``
    while the WS guard reports healthy and fills on day0 trades go unseen.
    The call site MUST pass ``min_hours_to_resolution=0.0`` so day0 markets
    are subscribed.
    """
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_AUTO_DERIVE", "1")
    monkeypatch.delenv("POLYMARKET_USER_WS_CONDITION_IDS", raising=False)

    captured: dict[str, object] = {"kwargs": None}

    def _recording_scanner(**kwargs):
        captured["kwargs"] = kwargs
        return [{"condition_ids": ["0xday0market"]}]

    monkeypatch.setattr(
        "src.data.market_scanner.find_weather_markets", _recording_scanner
    )

    zeus_main._start_user_channel_ingestor_if_enabled()

    assert captured["kwargs"] is not None, "scanner must be invoked"
    min_hours = captured["kwargs"].get("min_hours_to_resolution")
    assert min_hours is not None, (
        "call site must pass min_hours_to_resolution explicitly; "
        "default 6.0 would drop day0 markets from WS subscription"
    )
    assert min_hours <= 0.0 + 1e-9, (
        f"min_hours_to_resolution must be <=0 to include day0 markets; "
        f"got {min_hours!r}"
    )


def test_auto_derive_disabled_unless_master_toggle(monkeypatch, caplog):
    """Invariant #5: AUTO_DERIVE alone does not start the WS ingestor."""
    monkeypatch.delenv("ZEUS_USER_CHANNEL_WS_ENABLED", raising=False)
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_AUTO_DERIVE", "1")
    monkeypatch.delenv("POLYMARKET_USER_WS_CONDITION_IDS", raising=False)

    sentinel: dict[str, int] = {"calls": 0}

    def _scanner_called(**_kw):
        sentinel["calls"] += 1
        return [{"condition_ids": ["0xsomething"]}]

    monkeypatch.setattr(
        "src.data.market_scanner.find_weather_markets", _scanner_called
    )

    caplog.set_level(logging.WARNING, logger="zeus")
    zeus_main._start_user_channel_ingestor_if_enabled()

    # No ingestor constructed, scanner never invoked.
    assert sentinel["calls"] == 0
    # The standard "not configured" WARNING fires (boot path returns early).
    warning_messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "user-channel WS not configured" in msg for msg in warning_messages
    ), f"expected 'not configured' WARNING; saw {warning_messages!r}"


def test_ws_connect_bypasses_proxy(monkeypatch):
    """Invariant #6 (2026-05-01 live-blocker): _default_websocket_connect must
    pass proxy=None to websockets.connect().

    Root cause: websockets>=16 defaults proxy=True and auto-detects HTTPS_PROXY.
    The daemon plist sets HTTPS_PROXY=localhost:7890 (for REST calls) but the WS
    endpoint is NOT in NO_PROXY, so all WSS traffic routed through the dead local
    proxy → ConnectionClosedError within 2s. Fix: always pass proxy=None for WS.
    """
    import inspect
    from src.ingest.polymarket_user_channel import _default_websocket_connect

    captured: dict[str, Any] = {"kwargs": None}

    class _FakeWS:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

        def __await__(self):
            return iter([self])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    def _fake_connect(endpoint: str, **kwargs: Any) -> _FakeWS:
        captured["kwargs"] = kwargs
        return _FakeWS(endpoint, **kwargs)

    monkeypatch.setattr("websockets.connect", _fake_connect)

    # Call the function under test — it must pass proxy=None
    _default_websocket_connect("wss://ws-subscriptions-clob.polymarket.com/ws/user")

    assert captured["kwargs"] is not None, "_default_websocket_connect must call websockets.connect"
    assert "proxy" in captured["kwargs"], (
        "websockets.connect must be called with an explicit proxy= kwarg; "
        "omitting it lets websockets>=16 auto-detect HTTPS_PROXY and route WS "
        "traffic through the local proxy (dead at port 7890)"
    )
    assert captured["kwargs"]["proxy"] is None, (
        f"proxy kwarg must be None (bypass proxy); got {captured['kwargs']['proxy']!r}. "
        "websockets>=16 proxy=True (default) routes WSS through HTTPS_PROXY=localhost:7890 "
        "which causes ConnectionClosedError within 2s on the daemon."
    )


def test_ws_connect_skips_proxy_kwarg_on_older_websockets(monkeypatch):
    """PR #35 P1 review: older websockets (10.x/12.x) do not accept `proxy=`.

    Pre-fix, _default_websocket_connect unconditionally passed proxy=None which
    raised TypeError before any WS session opened. Post-fix, the function probes
    the connect signature and only forwards proxy=None when the kwarg is
    supported. This test mocks a connect function with a closed signature
    (no **kwargs, no proxy parameter) and asserts no proxy kwarg leaks through.
    """
    from src.ingest.polymarket_user_channel import _default_websocket_connect

    captured: dict[str, Any] = {"args": None, "kwargs": None}

    class _OldFakeWS:
        def __init__(self, endpoint: str) -> None:  # closed signature
            captured["endpoint"] = endpoint

        def __await__(self):
            return iter([self])

    def _old_connect(endpoint: str):
        captured["args"] = (endpoint,)
        captured["kwargs"] = {}
        return _OldFakeWS(endpoint)

    monkeypatch.setattr("websockets.connect", _old_connect)

    _default_websocket_connect("wss://ws-subscriptions-clob.polymarket.com/ws/user")

    assert captured["args"] == ("wss://ws-subscriptions-clob.polymarket.com/ws/user",), (
        "older websockets path must call connect(endpoint) positionally"
    )
    assert captured["kwargs"] == {}, (
        f"older websockets does not accept proxy=; got kwargs={captured['kwargs']!r}. "
        "TypeError would fire at connect time and force a perpetual reconnect-fail loop."
    )


def test_ws_auth_sourced_from_adapter_not_env(monkeypatch, _stub_ingestor):
    """Invariant #7 (2026-05-01 live-blocker): the live daemon must source WSAuth
    from adapter.sdk_client().creds, NOT from POLYMARKET_API_KEY env var.

    Root cause: POLYMARKET_API_KEY in the plist was stale (old key). The valid L2
    key is derived deterministically via create_or_derive_api_key() inside the
    adapter's SDK client. WSAuth.from_env() therefore produces invalid credentials
    and the WS server closes the connection immediately after the subscription
    message is sent (no close frame received or sent).

    This test verifies that the ingestor is constructed with the adapter-derived
    credentials, not the raw env-var credentials.
    """
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    monkeypatch.setenv("POLYMARKET_USER_WS_CONDITION_IDS", "0xcondition1")
    # Set stale/wrong env var creds that must NOT reach the ingestor.
    monkeypatch.setenv("POLYMARKET_API_KEY", "stale-env-key")
    monkeypatch.setenv("POLYMARKET_API_SECRET", "stale-env-secret")
    monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "stale-env-passphrase")

    # The adapter stub returns a client with DERIVED creds (different from env).
    class _DerivedCreds:
        api_key = "derived-key-from-adapter"
        api_secret = "derived-secret-from-adapter"
        api_passphrase = "derived-passphrase-from-adapter"

    class _StubSdkClient:
        creds = _DerivedCreds()

    class _StubAdapterWithCreds:
        def _sdk_client(self) -> _StubSdkClient:
            return _StubSdkClient()

    class _StubClientWithCreds:
        def _ensure_v2_adapter(self) -> _StubAdapterWithCreds:
            return _StubAdapterWithCreds()

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _StubClientWithCreds)

    # Capture the WSAuth that reaches the ingestor constructor.
    captured_auth: dict[str, Any] = {}

    class _CapturingIngestor(_FakeIngestor):
        def __init__(self, adapter: Any, condition_ids: list[str], **kwargs: Any) -> None:
            super().__init__(adapter, condition_ids)
            captured_auth.update(kwargs.get("auth").__dict__ if kwargs.get("auth") else {})

        @classmethod
        def from_env(cls, adapter: Any, condition_ids: list[str], **kwargs: Any) -> "_CapturingIngestor":
            # from_env must NOT be called in the adapter-creds path.
            raise AssertionError(
                "from_env must not be called; daemon must use adapter-derived creds, "
                "not WSAuth.from_env() which reads stale POLYMARKET_API_KEY env var"
            )

    monkeypatch.setattr(
        "src.ingest.polymarket_user_channel.PolymarketUserChannelIngestor",
        _CapturingIngestor,
    )

    zeus_main._start_user_channel_ingestor_if_enabled()

    assert captured_auth, "ingestor must be constructed with auth kwarg"
    assert captured_auth.get("api_key") == "derived-key-from-adapter", (
        f"WSAuth.api_key must come from adapter._sdk_client().creds, not env var; "
        f"got {captured_auth.get('api_key')!r}. "
        f"The stale env var 'stale-env-key' must never reach the WS subscription."
    )
    assert captured_auth.get("api_key") != "stale-env-key", (
        "daemon used stale POLYMARKET_API_KEY env var instead of adapter-derived creds"
    )
