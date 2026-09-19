"""A snapshot's cost must track unresolved WORK, not account lifetime.

`get_account_truth` paginated the account's whole trade history every tick. That
history only grows while the deadline stays fixed, so the read fails eventually
by construction -- it did on 2026-09-18 with 313 consecutive deadline elapses
and zero page-limit breaches, stalling capital recovery while a filled Shanghai
order sat unsourced for hours.

The window is derived from the candidate rows the snapshot is captured FOR, so
it is exactly as wide as the work and never wider than the evidence those rows
could need.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from src.execution.command_recovery import (
    _ACCOUNT_TRUTH_OBLIGATION_SLACK_DAYS,
    _obligation_window_epoch_seconds,
    _scheduled_venue_snapshot_kwargs,
)

UTC = timezone.utc


def _row(created_at: datetime) -> dict:
    return {"command_id": "c", "created_at": created_at.isoformat()}


class TestWindowDerivation:
    def test_window_is_the_oldest_candidate_minus_slack(self):
        oldest = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)
        newer = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)

        window = _obligation_window_epoch_seconds([_row(newer)], [_row(oldest)])

        expected = oldest - timedelta(days=_ACCOUNT_TRUTH_OBLIGATION_SLACK_DAYS)
        assert window == int(expected.timestamp())

    def test_the_window_never_starts_after_its_oldest_obligation(self):
        """The bound must not be tighter than the evidence a pass may need."""

        oldest = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)

        window = _obligation_window_epoch_seconds([_row(oldest)])

        assert window < int(oldest.timestamp())

    def test_no_candidates_means_no_bound(self):
        assert _obligation_window_epoch_seconds([], []) is None

    def test_an_unparseable_timestamp_falls_back_to_the_whole_history(self):
        """Fail OPEN to the previous behaviour: never narrow on bad input."""

        assert (
            _obligation_window_epoch_seconds([{"command_id": "c", "created_at": ""}])
            is None
        )
        assert (
            _obligation_window_epoch_seconds(
                [{"command_id": "c", "created_at": "not-a-time"}]
            )
            is None
        )

    def test_a_naive_timestamp_is_read_as_utc(self):
        """venue_commands.created_at is UTC; _parse_ts is the module convention."""

        naive = datetime(2026, 9, 11, 3, 0)
        window = _obligation_window_epoch_seconds(
            [{"command_id": "c", "created_at": naive.isoformat()}]
        )
        expected = naive.replace(tzinfo=UTC) - timedelta(
            days=_ACCOUNT_TRUTH_OBLIGATION_SLACK_DAYS
        )
        assert window == int(expected.timestamp())

    def test_one_bad_row_discards_the_bound_for_the_whole_group(self):
        good = _row(datetime(2026, 9, 11, 3, 0, tzinfo=UTC))
        bad = {"command_id": "c", "created_at": "garbage"}
        assert _obligation_window_epoch_seconds([good, bad]) is None


class TestSnapshotKwargs:
    def test_the_bound_reaches_the_snapshot_call(self):
        kwargs = _scheduled_venue_snapshot_kwargs(
            "live_tick",
            {"order_ids": set(), "idempotency_keys": set(), "condition_ids": set()},
            deadline_monotonic=time.monotonic() + 30.0,
            trades_after_epoch_seconds=1_789_000_000,
        )
        assert kwargs["trades_after_epoch_seconds"] == 1_789_000_000

    def test_absent_bound_is_not_passed_at_all(self):
        kwargs = _scheduled_venue_snapshot_kwargs(
            "live_tick",
            {"order_ids": set(), "idempotency_keys": set(), "condition_ids": set()},
            deadline_monotonic=time.monotonic() + 30.0,
        )
        assert "trades_after_epoch_seconds" not in kwargs


class TestTheBoundReachesTheVenue:
    """The window must become a server-side filter, not a local trim."""

    def test_the_adapter_sends_after_on_the_trade_surface_only(self, monkeypatch):
        import asyncio

        from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

        seen: list[tuple[str, dict]] = []

        async def capture(_self, _http, endpoint, *, headers, params,
                          deadline_monotonic):
            seen.append((endpoint, dict(params or {})))
            return {"data": [], "next_cursor": "LTE="}

        async def headers(_self, _http, _client, *, request_path,
                          deadline_monotonic, timestamp=None):
            return {"h": request_path}, timestamp or 1_700_000_000

        monkeypatch.setattr(
            PolymarketV2Adapter, "_account_truth_headers_async", headers
        )
        monkeypatch.setattr(
            PolymarketV2Adapter, "_account_truth_json_get_async", capture
        )
        monkeypatch.setattr(PolymarketV2Adapter, "_sdk_client", lambda self: object())
        monkeypatch.setattr(
            PolymarketV2Adapter, "host", "https://clob.test", raising=False
        )
        monkeypatch.setattr(
            PolymarketV2Adapter, "_open_order_states", lambda self, rows: ()
        )
        monkeypatch.setattr(PolymarketV2Adapter, "_trade_facts", lambda self, rows: ())

        adapter = PolymarketV2Adapter.__new__(PolymarketV2Adapter)
        adapter.get_account_truth(
            deadline_monotonic=time.monotonic() + 30.0,
            trades_after_epoch_seconds=1_789_000_000,
        )

        trades = [p for endpoint, p in seen if endpoint.endswith("/trades")]
        orders = [p for endpoint, p in seen if endpoint.endswith("/orders")]
        assert trades and all(p.get("after") == "1789000000" for p in trades), (
            "the trade surface must carry the server-side lower bound"
        )
        assert orders and all("after" not in p for p in orders), (
            "open orders are the live book, never a history scan; do not bound them"
        )

    def test_without_a_bound_no_after_is_sent(self, monkeypatch):
        from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

        seen: list[dict] = []

        async def capture(_self, _http, endpoint, *, headers, params,
                          deadline_monotonic):
            seen.append(dict(params or {}))
            return {"data": [], "next_cursor": "LTE="}

        async def headers(_self, _http, _client, *, request_path,
                          deadline_monotonic, timestamp=None):
            return {"h": request_path}, timestamp or 1_700_000_000

        monkeypatch.setattr(
            PolymarketV2Adapter, "_account_truth_headers_async", headers
        )
        monkeypatch.setattr(
            PolymarketV2Adapter, "_account_truth_json_get_async", capture
        )
        monkeypatch.setattr(PolymarketV2Adapter, "_sdk_client", lambda self: object())
        monkeypatch.setattr(
            PolymarketV2Adapter, "host", "https://clob.test", raising=False
        )
        monkeypatch.setattr(
            PolymarketV2Adapter, "_open_order_states", lambda self, rows: ()
        )
        monkeypatch.setattr(PolymarketV2Adapter, "_trade_facts", lambda self, rows: ())

        adapter = PolymarketV2Adapter.__new__(PolymarketV2Adapter)
        adapter.get_account_truth(deadline_monotonic=time.monotonic() + 30.0)

        assert seen and all("after" not in p for p in seen)
