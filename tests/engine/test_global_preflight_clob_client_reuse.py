# Created: 2026-10-02
# Last audited: 2026-10-02
# Authority basis: FC-03 submit-time refetch (root AGENTS.md §0); global winner
#                  preflight transport reuse (event_reactor_adapter._global_preflight_clob_client)
"""Global winner preflight reuses one warm CLOB transport and still refetches."""

from __future__ import annotations

import threading

import pytest

import src.engine.event_reactor_adapter as era
from src.data.polymarket_request_governor import RequestPriority
from tests.integration.test_w3_solve_seam_g3 import (
    test_global_preflight_jit_worse_curve_replaces_and_reauctions as _jit_flow,
)


@pytest.fixture(autouse=True)
def _fresh_pool(monkeypatch):
    monkeypatch.setattr(era, "_GLOBAL_PREFLIGHT_CLOB_CLIENTS", {}, raising=False)


def _counting_client(monkeypatch):
    built: list[dict] = []
    fetched: list[str] = []
    closed: list[int] = []

    class Clob:
        def __init__(self, **kwargs):
            built.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            closed.append(1)
            return False

        def close(self):
            closed.append(1)

        def get_clob_market_info(self, condition_id, *, timeout=None):
            fetched.append(condition_id)
            return {}

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Clob)
    return built, fetched, closed


def test_preflight_clob_client_is_built_once_and_every_call_refetches(monkeypatch):
    built, fetched, closed = _counting_client(monkeypatch)

    for _ in range(3):
        client = era._global_preflight_clob_client(
            priority=RequestPriority.SUBMIT_JIT, timeout_seconds=8.0
        )
        client.get_clob_market_info("condition-a")

    assert len(built) == 1
    assert built[0] == {
        "public_http_timeout": 8.0,
        "public_request_priority": RequestPriority.SUBMIT_JIT,
    }
    assert fetched == ["condition-a"] * 3  # FC-03: no response cache
    assert closed == []


def test_preflight_clob_client_keeps_priority_and_timeout_distinct(monkeypatch):
    built, _fetched, _closed = _counting_client(monkeypatch)

    held = era._global_preflight_clob_client(
        priority=RequestPriority.HELD_REDUCE_ONLY, timeout_seconds=8.0
    )
    entry = era._global_preflight_clob_client(
        priority=RequestPriority.SUBMIT_JIT, timeout_seconds=8.0
    )
    slower = era._global_preflight_clob_client(
        priority=RequestPriority.SUBMIT_JIT, timeout_seconds=12.0
    )

    assert len({id(held), id(entry), id(slower)}) == 3
    assert [b["public_request_priority"] for b in built] == [
        RequestPriority.HELD_REDUCE_ONLY,
        RequestPriority.SUBMIT_JIT,
        RequestPriority.SUBMIT_JIT,
    ]


def test_preflight_clob_client_concurrent_first_use_builds_one(monkeypatch):
    built, _fetched, _closed = _counting_client(monkeypatch)
    barrier = threading.Barrier(8)
    seen: list[int] = []

    def worker():
        barrier.wait()
        seen.append(
            id(
                era._global_preflight_clob_client(
                    priority=RequestPriority.SUBMIT_JIT, timeout_seconds=8.0
                )
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(built) == 1
    assert len(set(seen)) == 1


def test_buy_jit_preflight_builds_one_clob_client_across_winners(monkeypatch):
    import src.data.polymarket_client as polymarket_client

    constructed: list[object] = []
    real_setattr = monkeypatch.setattr

    def counting_setattr(target, *args, **kwargs):
        # The shared fixture installs its FakeClob by dotted path; wrap it so
        # every construction is counted while its behavior stays unchanged.
        if target == "src.data.polymarket_client.PolymarketClient":
            fake = args[0]

            class Counted(fake):
                def __init__(self, **kw):
                    constructed.append(self)
                    super().__init__(**kw)

            return real_setattr(polymarket_client, "PolymarketClient", Counted)
        return real_setattr(target, *args, **kwargs)

    monkeypatch.setattr = counting_setattr
    # Drives _global_preflight_entry_jit_receipt through two fresh-fetch
    # winners (the superseded curve, then the stable re-fetch).
    _jit_flow(monkeypatch)

    assert len(constructed) == 1
