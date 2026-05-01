# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: live alpha capture directive 2026-05-01 — replace hardcoded
#                  POLYMARKET_USER_WS_CONDITION_IDS with auto-derivation from
#                  the canonical market scanner, so the daemon subscribes only
#                  to markets it can trade and the subscription list cannot
#                  drift from on-chain truth as markets rotate.
"""Antibody tests for user-channel WS condition_ids auto-derive (2026-05-01).

Pins five cross-module invariants:

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


class _FakeAdapter:
    """Stub adapter — never used because we monkeypatch the ingestor too."""


class _FakeIngestor:
    instances: list["_FakeIngestor"] = []

    def __init__(self, adapter: Any, condition_ids: list[str]) -> None:
        self.adapter = adapter
        self.condition_ids = list(condition_ids)
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
