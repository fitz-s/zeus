# Created: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "batch submit + safe prefixes" + architecture/invariants.yaml INV-28
#   -- W2.1 packet (inert, no production call site).
"""W2.1 batch cancel orchestrator: INV-28 persist-before-side-effect
discipline at batch shape, chunking, mapping precedence, and partial-batch
failure semantics for cancel_commands_batch.

The batch SUBMIT orchestrator (``submit_orders_batch``) this file used to
also cover was deleted as dead code in the gate-stack simplification
(Phase 1, 2026-07-06) -- zero live callers."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.execution.batch_order_submission import cancel_commands_batch

_NOW = datetime(2026, 7, 2, tzinfo=timezone.utc)


@pytest.fixture
def mem_conn():
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    yield c
    c.close()


def _ensure_snapshot(conn, *, token_id: str = "yes-token", snapshot_id: str = "snap-1") -> str:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    if get_snapshot(conn, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        conn,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-test",
            event_id="event-test",
            event_slug="event-test",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            fee_details={
                "source": "test",
                "token_id": token_id,
                "fee_rate_fraction": 0.0,
                "fee_rate_bps": 0.0,
                "fee_rate_source_field": "fee_rate_fraction",
                "fee_rate_raw_unit": "fraction",
            },
            token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.56"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


class FakeGatewayClient:
    """Duck-typed gateway fake: place_limit_orders_batch / cancel_orders_batch.

    ``submit_responses`` is a list of per-call response lists (one entry
    consumed per invocation) OR a single list reused for every call.
    ``fail_on_call_index`` (0-based) makes that specific call raise instead.
    """

    def __init__(
        self,
        submit_responses=None,
        cancel_responses=None,
        fail_submit_on_call_index: int | None = None,
        fail_cancel_on_call_index: int | None = None,
        submit_exception: BaseException | None = None,
        cancel_exception: BaseException | None = None,
    ):
        self.submit_responses = submit_responses or []
        self.cancel_responses = cancel_responses or []
        self.fail_submit_on_call_index = fail_submit_on_call_index
        self.fail_cancel_on_call_index = fail_cancel_on_call_index
        self.submit_exception = submit_exception or TimeoutError("submit_batch timed out")
        self.cancel_exception = cancel_exception or TimeoutError("cancel_batch timed out")
        self.submit_calls: list[list[Any]] = []
        self.cancel_calls: list[list[str]] = []

    def place_limit_orders_batch(self, envelopes):
        call_index = len(self.submit_calls)
        self.submit_calls.append(list(envelopes))
        if call_index == self.fail_submit_on_call_index:
            raise self.submit_exception
        return self.submit_responses[call_index]

    def cancel_orders_batch(self, order_ids):
        call_index = len(self.cancel_calls)
        self.cancel_calls.append(list(order_ids))
        if call_index == self.fail_cancel_on_call_index:
            raise self.cancel_exception
        return self.cancel_responses[call_index]


class TestInv24Allowlist:
    def test_inv24_allowlist_includes_batch_orchestrator(self):
        import src.data.polymarket_client as pc

        allowed_rel = {
            p.replace(str(pc._INV24_REPO_ROOT) + "/", "") for p in pc._INV24_ALLOWED_CALLER_ABS_PATHS
        }
        assert "src/execution/batch_order_submission.py" in allowed_rel


# ---------------------------------------------------------------------------
# cancel_commands_batch
# ---------------------------------------------------------------------------


def _seed_ackable_command(conn, *, command_id: str, token_id: str = "yes-token", venue_order_id: str = "vord-0") -> None:
    """Persist a command through ACKED state with a venue_order_id, the
    precondition cancel_commands_batch requires (mirrors
    request_cancel_for_command's own precondition)."""
    from src.execution.command_bus import IntentKind as _IntentKind
    from src.state.venue_command_repo import append_event, insert_command, insert_submission_envelope
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    snapshot_id = _ensure_snapshot(conn, token_id=token_id, snapshot_id=f"snap-{command_id}")
    envelope_id = f"env-{command_id}"
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2", sdk_version="test", host="https://clob-v2.polymarket.com",
            chain_id=137, funder_address="0xfunder", condition_id="condition-test", question_id="question-test",
            yes_token_id=token_id, no_token_id=f"{token_id}-no", selected_outcome_token_id=token_id,
            outcome_label="YES", side="SELL", price=Decimal("0.50"), size=Decimal("10"), order_type="GTC",
            post_only=False, tick_size=Decimal("0.01"), min_order_size=Decimal("0.01"), neg_risk=False,
            fee_details={"source": "test", "token_id": token_id, "fee_rate_fraction": 0.0, "fee_rate_bps": 0.0,
                         "fee_rate_source_field": "fee_rate_fraction", "fee_rate_raw_unit": "fraction"},
            canonical_pre_sign_payload_hash="a" * 64, signed_order=None, signed_order_hash=None,
            raw_request_hash="b" * 64, raw_response_json=None, order_id=None, trade_ids=(), transaction_hashes=(),
            error_code=None, error_message=None, captured_at=_NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    insert_command(
        conn, command_id=command_id, snapshot_id=snapshot_id, envelope_id=envelope_id, position_id="pos-0",
        decision_id="decision-cancel", idempotency_key=command_id.ljust(32, "0")[:32],
        intent_kind=_IntentKind.EXIT.value, market_id="market-123", token_id=token_id, side="SELL",
        size=10.0, price=0.50, created_at=_NOW.isoformat(), snapshot_checked_at=_NOW.isoformat(),
    )
    now = _NOW.isoformat()
    append_event(conn, command_id=command_id, event_type="SUBMIT_REQUESTED", occurred_at=now, payload={"batch": True})
    append_event(
        conn, command_id=command_id, event_type="SUBMIT_ACKED", occurred_at=now,
        payload={"order_id": venue_order_id, "batch": True},
    )


def _acked(venue_order_id: str) -> dict:
    return {"canceled": True, "orderID": venue_order_id}


def _not_canceled(venue_order_id: str, reason: str = "already filled") -> dict:
    return {"not_canceled": reason, "orderID": venue_order_id}


class TestCancelCommandsBatchPersistBeforeCall:
    def test_cancel_requested_committed_before_sdk_call(self, mem_conn):
        _seed_ackable_command(mem_conn, command_id="cmd-cancel-0", venue_order_id="vord-0")
        seen = {}

        class SpyClient(FakeGatewayClient):
            def cancel_orders_batch(self, order_ids):
                rows = mem_conn.execute(
                    "SELECT state FROM venue_commands WHERE command_id = 'cmd-cancel-0'"
                ).fetchall()
                seen["state_at_call_time"] = rows[0][0]
                return super().cancel_orders_batch(order_ids)

        client = SpyClient(cancel_responses=[[_acked("vord-0")]])
        outcomes = cancel_commands_batch(mem_conn, client, ["cmd-cancel-0"])

        assert outcomes[0].status == "acked"
        assert seen["state_at_call_time"] == "CANCEL_PENDING"


class TestCancelCommandsBatchMapping:
    def test_acked_and_not_canceled_map_correctly(self, mem_conn):
        _seed_ackable_command(mem_conn, command_id="cmd-0", venue_order_id="vord-0")
        _seed_ackable_command(mem_conn, command_id="cmd-1", venue_order_id="vord-1")
        client = FakeGatewayClient(cancel_responses=[[_acked("vord-0"), _not_canceled("vord-1")]])

        outcomes = cancel_commands_batch(mem_conn, client, ["cmd-0", "cmd-1"])

        assert [o.status for o in outcomes] == ["acked", "not_canceled"]

    def test_not_requestable_command_skipped_without_blocking_chunk(self, mem_conn):
        _seed_ackable_command(mem_conn, command_id="cmd-good", venue_order_id="vord-good")
        client = FakeGatewayClient(cancel_responses=[[_acked("vord-good")]])

        outcomes = cancel_commands_batch(mem_conn, client, ["cmd-missing", "cmd-good"])

        assert outcomes[0].status == "not_requestable"
        assert outcomes[1].status == "acked"
        assert client.cancel_calls == [["vord-good"]]


class TestCancelCommandsBatchPartialFailure:
    def test_sdk_exception_marks_ambiguous_and_halts_later_chunks(self, mem_conn):
        _seed_ackable_command(mem_conn, command_id="cmd-a", venue_order_id="vord-a")
        _seed_ackable_command(mem_conn, command_id="cmd-b", venue_order_id="vord-b")
        client = FakeGatewayClient(cancel_responses=[None], fail_cancel_on_call_index=0)

        outcomes = cancel_commands_batch(mem_conn, client, ["cmd-a", "cmd-b"])

        # Both requestable commands are in the SAME chunk (well under
        # MAX_ORDERS_PER_BATCH) -- exercised together to prove ambiguous
        # failure applies to the whole chunk uniformly.
        assert all(o.status == "unknown" for o in outcomes)
        events = mem_conn.execute(
            "SELECT command_id FROM venue_command_events WHERE event_type = 'CANCEL_REPLACE_BLOCKED'"
        ).fetchall()
        assert sorted(r[0] for r in events) == ["cmd-a", "cmd-b"]
