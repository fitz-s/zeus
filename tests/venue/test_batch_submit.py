# Created: 2026-07-02
# Authority basis: docs/rebuild/order_engine_first_principles_design_2026-07-02.md
#   §3.3 (safe prefixes, lines 118-121) + docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "batch submit + safe prefixes" -- W2.1 packet.
"""W2.1 pure batch-submit primitives: chunking, safe-prefix decomposition,
fail-closed response mapping. No I/O, no SDK, no DB -- see
src/venue/batch_submit.py module docstring for the full design rationale."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.venue.batch_submit import (
    MAX_ORDERS_PER_BATCH,
    BatchMappedItem,
    PlannedBatchOrder,
    chunk_orders,
    compute_safe_prefixes,
    map_batch_items,
)


# ---------------------------------------------------------------------------
# chunk_orders
# ---------------------------------------------------------------------------

class TestChunkOrders:
    def test_empty_input_yields_no_chunks(self):
        assert chunk_orders([]) == []

    def test_single_item_yields_one_chunk(self):
        assert chunk_orders([1]) == [[1]]

    def test_exactly_max_batch_size_yields_one_chunk(self):
        items = list(range(MAX_ORDERS_PER_BATCH))
        chunks = chunk_orders(items)
        assert chunks == [items]
        assert len(chunks) == 1

    def test_one_over_max_batch_size_yields_two_chunks(self):
        items = list(range(MAX_ORDERS_PER_BATCH + 1))
        chunks = chunk_orders(items)
        assert len(chunks) == 2
        assert chunks[0] == items[:MAX_ORDERS_PER_BATCH]
        assert chunks[1] == items[MAX_ORDERS_PER_BATCH:]

    def test_45_items_yields_three_full_chunks(self):
        items = list(range(45))
        chunks = chunk_orders(items)
        assert len(chunks) == 3
        assert [len(c) for c in chunks] == [15, 15, 15]
        assert sum(chunks, []) == items

    def test_preserves_order(self):
        items = ["z", "a", "m"]
        assert chunk_orders(items, chunk_size=2) == [["z", "a"], ["m"]]

    def test_zero_chunk_size_raises(self):
        with pytest.raises(ValueError):
            chunk_orders([1, 2], chunk_size=0)

    def test_negative_chunk_size_raises(self):
        with pytest.raises(ValueError):
            chunk_orders([1, 2], chunk_size=-1)


# ---------------------------------------------------------------------------
# compute_safe_prefixes
# ---------------------------------------------------------------------------

def _planned(*deltas: str) -> list[PlannedBatchOrder]:
    return [
        PlannedBatchOrder(order_ref=f"order-{i}", exposure_delta=Decimal(d))
        for i, d in enumerate(deltas)
    ]


class TestComputeSafePrefixes:
    def test_empty_input_yields_no_batches(self):
        assert compute_safe_prefixes([], is_acceptable=lambda c: True) == []

    def test_single_acceptable_order_yields_one_batch(self):
        orders = _planned("10")
        batches = compute_safe_prefixes(orders, is_acceptable=lambda c: c <= Decimal(100))
        assert batches == [["order-0"]]

    def test_single_unacceptable_order_yields_no_batch(self):
        orders = _planned("500")
        batches = compute_safe_prefixes(orders, is_acceptable=lambda c: c <= Decimal(100))
        assert batches == []

    def test_all_unacceptable_yields_no_batch(self):
        orders = _planned("500", "500", "500")
        batches = compute_safe_prefixes(orders, is_acceptable=lambda c: c <= Decimal(100))
        assert batches == []

    def test_every_prefix_is_acceptable_property(self):
        # Cap at 100; each order adds 30 -- acceptable cumulative cuts are
        # at 30, 60, 90 (all <= 100); 120 would not be. The 4th order has
        # no acceptable cut point reachable from it (90+30=120 > cap) and
        # is correctly excluded -- never submit past the last safe prefix.
        orders = _planned("30", "30", "30", "30")
        cap = Decimal(100)
        batches = compute_safe_prefixes(orders, is_acceptable=lambda c: c <= cap)
        # Verify the safe-prefix PROPERTY directly: cumulative sum after
        # each returned batch boundary must be acceptable.
        running = Decimal(0)
        seen = []
        for batch in batches:
            running += Decimal(30) * len(batch)
            assert running <= cap
            seen.extend(batch)
        assert seen == [o.order_ref for o in orders[:3]]
        assert "order-3" not in seen

    def test_maximizes_batch_size_within_window(self):
        # cap=100, delta=10 per order, 12 orders (unbounded by chunk size
        # since max_batch_size=15 default > 12). Every cumulative point
        # 10..120 in steps of 10 is a candidate; only <=100 acceptable.
        # Expect ONE batch of 10 orders (cumulative 100), remainder (2
        # orders, would push to 120) has no acceptable cut -> excluded.
        orders = _planned(*(["10"] * 12))
        batches = compute_safe_prefixes(orders, is_acceptable=lambda c: c <= Decimal(100))
        assert len(batches) == 1
        assert len(batches[0]) == 10

    def test_respects_max_batch_size_ceiling(self):
        # Acceptability predicate would allow arbitrarily large cumulative
        # exposure (always True) -- max_batch_size must still cap each cut.
        orders = _planned(*(["1"] * 20))
        batches = compute_safe_prefixes(orders, is_acceptable=lambda c: True, max_batch_size=15)
        assert [len(b) for b in batches] == [15, 5]

    def test_zero_max_batch_size_raises(self):
        with pytest.raises(ValueError):
            compute_safe_prefixes(_planned("1"), is_acceptable=lambda c: True, max_batch_size=0)

    def test_returns_order_refs_not_wrapper_objects(self):
        orders = _planned("1")
        batches = compute_safe_prefixes(orders, is_acceptable=lambda c: True)
        assert batches == [["order-0"]]
        assert not isinstance(batches[0][0], PlannedBatchOrder)


# ---------------------------------------------------------------------------
# map_batch_items -- fail-closed mapping precedence
# ---------------------------------------------------------------------------

class TestMapBatchItemsEchoId:
    def test_all_items_echo_matched_maps_by_echo_id(self):
        response = [
            {"orderHash": "hash-b", "status": "LIVE"},
            {"orderHash": "hash-a", "status": "LIVE"},
        ]
        mapped = map_batch_items(response, echo_keys=["hash-a", "hash-b"])
        assert [m.source for m in mapped] == ["echo_id", "echo_id"]
        assert mapped[0].raw_item == {"orderHash": "hash-a", "status": "LIVE"}
        assert mapped[1].raw_item == {"orderHash": "hash-b", "status": "LIVE"}

    def test_echo_id_out_of_order_response_still_maps_correctly(self):
        # This is the point of echo-id mapping: response array order need
        # not match request order.
        response = [
            {"order_hash": "hash-z"},
            {"order_hash": "hash-x"},
            {"order_hash": "hash-y"},
        ]
        mapped = map_batch_items(response, echo_keys=["hash-x", "hash-y", "hash-z"])
        assert [m.raw_item["order_hash"] for m in mapped] == ["hash-x", "hash-y", "hash-z"]
        assert all(m.source == "echo_id" for m in mapped)


class TestMapBatchItemsIndexFallback:
    def test_no_recognizable_echo_fields_falls_back_to_index_with_matching_length(self):
        response = [{"status": "LIVE"}, {"status": "REJECTED"}]
        mapped = map_batch_items(response, echo_keys=["hash-a", "hash-b"])
        assert [m.source for m in mapped] == ["index", "index"]
        assert mapped[0].raw_item == {"status": "LIVE"}
        assert mapped[1].raw_item == {"status": "REJECTED"}

    def test_partial_echo_match_falls_back_to_index_when_length_matches(self):
        # Only one item echoes a recognizable field -- untrustworthy as a
        # full echo-id mapping, but index fallback still applies since
        # lengths match.
        response = [{"orderHash": "hash-a"}, {"status": "REJECTED"}]
        mapped = map_batch_items(response, echo_keys=["hash-a", "hash-b"])
        assert [m.source for m in mapped] == ["index", "index"]


class TestMapBatchItemsUnmappedFailClosed:
    def test_non_array_response_is_unmapped_for_all(self):
        mapped = map_batch_items({"error": "boom"}, echo_keys=["a", "b", "c"])
        assert len(mapped) == 3
        assert all(m.source == "unmapped" and m.raw_item is None for m in mapped)

    def test_none_response_is_unmapped_for_all(self):
        mapped = map_batch_items(None, echo_keys=["a", "b"])
        assert all(m.source == "unmapped" for m in mapped)

    def test_length_mismatch_is_unmapped_for_all(self):
        response = [{"status": "LIVE"}]
        mapped = map_batch_items(response, echo_keys=["a", "b", "c"])
        assert len(mapped) == 3
        assert all(m.source == "unmapped" and m.raw_item is None for m in mapped)

    def test_partial_echo_match_with_length_mismatch_is_unmapped(self):
        response = [{"orderHash": "hash-a"}]
        mapped = map_batch_items(response, echo_keys=["hash-a", "hash-b"])
        assert all(m.source == "unmapped" for m in mapped)

    def test_empty_response_with_nonempty_requests_is_unmapped(self):
        mapped = map_batch_items([], echo_keys=["a", "b"])
        assert all(m.source == "unmapped" for m in mapped)

    def test_empty_response_with_empty_requests_maps_trivially(self):
        assert map_batch_items([], echo_keys=[]) == []


class TestMapBatchItemsCancelCandidateFields:
    def test_cancel_echo_fields_resolve_by_order_id(self):
        from src.venue.batch_submit import CANCEL_ECHO_CANDIDATE_FIELDS

        response = [{"orderID": "ord-2"}, {"orderID": "ord-1"}]
        mapped = map_batch_items(
            response,
            echo_keys=["ord-1", "ord-2"],
            echo_candidate_fields=CANCEL_ECHO_CANDIDATE_FIELDS,
        )
        assert [m.raw_item["orderID"] for m in mapped] == ["ord-1", "ord-2"]
        assert all(m.source == "echo_id" for m in mapped)


# ---------------------------------------------------------------------------
# map_cancel_envelope -- LIVE-VERIFIED cancel_orders envelope (2026-07-05)
# ---------------------------------------------------------------------------

class TestMapCancelEnvelope:
    """Pin the live DELETE /orders response shape observed 2026-07-05 on
    commands 12e0ee45e0a44bc8 / 1a74acd884cf4ba5: ONE envelope dict
    {"canceled": [...], "not_canceled": {...}} for the whole batch. The
    old per-item-array assumption sent a genuinely-canceled first live
    order to REVIEW_REQUIRED via BATCH_RESPONSE_UNMAPPED."""

    def test_live_shape_single_canceled_order_maps(self):
        from src.venue.batch_submit import map_cancel_envelope

        oid = "0x9df6b4f0b7cd1246f91fec5ba34943c74837284fe5c7c02e53bdc75a4f32939b"
        mapped = map_cancel_envelope({"canceled": [oid]}, [oid])
        assert mapped is not None
        assert mapped[0].source == "envelope"
        assert mapped[0].raw_item["canceled"] == [oid]

    def test_mixed_canceled_and_not_canceled(self):
        from src.venue.batch_submit import map_cancel_envelope

        raw = {"canceled": ["ord-0"], "not_canceled": {"ord-1": "order not found"}}
        mapped = map_cancel_envelope(raw, ["ord-0", "ord-1"])
        assert mapped[0].source == "envelope"
        assert mapped[0].raw_item["canceled"] == ["ord-0"]
        assert mapped[1].source == "envelope"
        assert mapped[1].raw_item["not_canceled"] == {"ord-1": "order not found"}

    def test_id_in_neither_collection_is_unmapped_fail_closed(self):
        from src.venue.batch_submit import map_cancel_envelope

        mapped = map_cancel_envelope({"canceled": ["ord-0"]}, ["ord-0", "ord-ghost"])
        assert mapped[0].source == "envelope"
        assert mapped[1].source == "unmapped"
        assert mapped[1].raw_item is None

    def test_non_envelope_dict_returns_none_for_fallthrough(self):
        from src.venue.batch_submit import map_cancel_envelope

        assert map_cancel_envelope({"error": "malformed"}, ["ord-0"]) is None
        assert map_cancel_envelope([{"orderID": "ord-0"}], ["ord-0"]) is None
        assert map_cancel_envelope(None, ["ord-0"]) is None

    def test_british_spelling_cancelled_accepted(self):
        from src.venue.batch_submit import map_cancel_envelope

        mapped = map_cancel_envelope({"cancelled": ["ord-0"]}, ["ord-0"])
        assert mapped is not None
        assert mapped[0].source == "envelope"

    def test_empty_envelope_maps_all_unmapped_not_none(self):
        from src.venue.batch_submit import map_cancel_envelope

        # {"canceled": []} IS the envelope shape (venue answered, canceled
        # nothing) -- ids must be unmapped, NOT fall through to index
        # mapping which could misattribute.
        mapped = map_cancel_envelope({"canceled": []}, ["ord-0"])
        assert mapped is not None
        assert mapped[0].source == "unmapped"
