"""One account snapshot must not spend its deadline twice.

`get_account_truth` paginates ORDERS and TRADES.  Both are signed up-front from
a single server timestamp, so neither depends on the other's result.  Running
them in sequence makes the snapshot cost the SUM of two surfaces against one
shared deadline -- and because an account's trade history only grows, that cost
rises with account lifetime while the budget stays fixed.  On 2026-09-18 that
produced 313 consecutive `INCOMPLETE_ACCOUNT_TRUTH: account snapshot deadline
elapsed` failures (and zero page-limit failures), stalling capital recovery for
hours with a filled order unsourced.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.venue.polymarket_v2_adapter import (
    IncompleteAccountTruthError,
    PolymarketV2Adapter,
)


class _Recorder:
    """Record when each surface's pages are in flight."""

    def __init__(self, *, page_seconds: float, pages_per_surface: int) -> None:
        self.page_seconds = page_seconds
        self.pages_per_surface = pages_per_surface
        self.inflight = 0
        self.max_inflight = 0
        self.calls: list[str] = []

    async def __call__(
        self, _http, endpoint, *, headers, params, deadline_monotonic
    ):
        surface = "orders" if endpoint.endswith("/orders") else "trades"
        if endpoint.endswith("/time"):
            return {"time": 1_700_000_000}
        self.calls.append(surface)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(self.page_seconds)
        finally:
            self.inflight -= 1
        index = sum(1 for call in self.calls if call == surface)
        last = index >= self.pages_per_surface
        return {"data": [], "next_cursor": "LTE=" if last else f"c{index}"}


@pytest.fixture(name="adapter")
def _adapter() -> PolymarketV2Adapter:
    return PolymarketV2Adapter.__new__(PolymarketV2Adapter)


def _install(monkeypatch, adapter, recorder) -> None:
    async def headers(_self, _http, _client, *, request_path, deadline_monotonic,
                      timestamp=None):
        return {"h": request_path}, timestamp or 1_700_000_000

    monkeypatch.setattr(
        PolymarketV2Adapter, "_account_truth_headers_async", headers
    )
    async def page(_self, http, endpoint, *, headers, params, deadline_monotonic):
        return await recorder(
            http,
            endpoint,
            headers=headers,
            params=params,
            deadline_monotonic=deadline_monotonic,
        )

    monkeypatch.setattr(PolymarketV2Adapter, "_account_truth_json_get_async", page)
    monkeypatch.setattr(PolymarketV2Adapter, "_sdk_client", lambda self: object())
    monkeypatch.setattr(PolymarketV2Adapter, "host", "https://clob.test", raising=False)
    monkeypatch.setattr(
        PolymarketV2Adapter, "_open_order_states", lambda self, rows: ()
    )
    monkeypatch.setattr(PolymarketV2Adapter, "_trade_facts", lambda self, rows: ())


class TestSnapshotOverlapsItsSurfaces:
    def test_both_surfaces_are_in_flight_together(self, monkeypatch, adapter):
        recorder = _Recorder(page_seconds=0.02, pages_per_surface=3)
        _install(monkeypatch, adapter, recorder)

        adapter.get_account_truth(deadline_monotonic=time.monotonic() + 30.0)

        assert recorder.max_inflight == 2, (
            "ORDERS and TRADES are independently signed and must paginate "
            "together; sequential pagination spends the shared deadline twice"
        )

    def test_wall_clock_is_the_slower_surface_not_their_sum(
        self, monkeypatch, adapter
    ):
        pages, page_seconds = 4, 0.05
        recorder = _Recorder(page_seconds=page_seconds, pages_per_surface=pages)
        _install(monkeypatch, adapter, recorder)

        started = time.monotonic()
        adapter.get_account_truth(deadline_monotonic=started + 30.0)
        elapsed = time.monotonic() - started

        sequential = 2 * pages * page_seconds
        assert elapsed < sequential * 0.75, (
            f"snapshot took {elapsed:.3f}s; sequential would be ~{sequential:.3f}s"
        )

    def test_every_page_of_both_surfaces_is_still_read(self, monkeypatch, adapter):
        recorder = _Recorder(page_seconds=0.0, pages_per_surface=5)
        _install(monkeypatch, adapter, recorder)

        adapter.get_account_truth(deadline_monotonic=time.monotonic() + 30.0)

        assert recorder.calls.count("orders") == 5
        assert recorder.calls.count("trades") == 5

    def test_an_incomplete_surface_still_fails_the_whole_snapshot(
        self, monkeypatch, adapter
    ):
        """Concurrency must not turn a partial read into accepted truth."""

        async def failing(_self, _http, endpoint, *, headers, params,
                          deadline_monotonic):
            if endpoint.endswith("/trades"):
                raise IncompleteAccountTruthError(
                    "INCOMPLETE_ACCOUNT_TRUTH: account snapshot deadline elapsed"
                )
            return {"data": [], "next_cursor": "LTE="}

        recorder = _Recorder(page_seconds=0.0, pages_per_surface=1)
        _install(monkeypatch, adapter, recorder)
        monkeypatch.setattr(
            PolymarketV2Adapter, "_account_truth_json_get_async", failing
        )

        with pytest.raises(IncompleteAccountTruthError):
            adapter.get_account_truth(deadline_monotonic=time.monotonic() + 30.0)
