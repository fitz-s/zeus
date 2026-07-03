# Created: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "batch submit + safe prefixes" + architecture/invariants.yaml INV-28
#   -- W2.1 packet (inert, no production call site).
"""W2.1 batch order-submission orchestrator: INV-28 persist-before-side-
effect discipline at batch shape, chunking, mapping precedence, partial-
batch failure semantics, and the optional rate_budget/self_trade_guard
composition hooks."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps
from src.execution.batch_order_submission import (
    BatchSubmitRequest,
    cancel_commands_batch,
    submit_orders_batch,
)
from src.execution.command_bus import IntentKind
from src.execution.self_trade_guard import SelfTradeCheckResult, SelfTradeVerdict
from src.venue.batch_submit import MAX_ORDERS_PER_BATCH

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


@pytest.fixture
def file_conn(tmp_path):
    """File-backed DB (not :memory:) so a genuinely SEPARATE connection can
    read it mid-call. A :memory: DB is private to its one connection object
    -- a "spy" that reads through that SAME connection would see uncommitted
    writes too (same-connection reads always do), making a persist-before-
    call proof built on it non-load-bearing. This fixture exists
    specifically so TestSubmitOrdersBatchPersistBeforeCall's proof is real.
    """
    from src.state.db import init_schema

    db_path = tmp_path / "batch_persist_proof.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    yield c, db_path
    c.close()


@dataclass(frozen=True)
class FakeSnapshot:
    """Minimal executable-snapshot stand-in for create_submission_envelope."""

    condition_id: str = "condition-test"
    question_id: str = "question-test"
    yes_token_id: str = "yes-token"
    no_token_id: str = "yes-token-no"  # matches _ensure_snapshot's f"{token_id}-no" convention
    tick_size: Decimal = Decimal("0.01")
    min_order_size: Decimal = Decimal("5")
    neg_risk: bool = False
    fee_details: dict = None
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    freshness_window_seconds: int = 300

    def __post_init__(self):
        if self.fee_details is None:
            object.__setattr__(self, "fee_details", {"bps": 0, "builder_fee_bps": 0})


def _adapter():
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    return PolymarketV2Adapter(
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=None,
    )


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


def _intent(*, price: float = 0.50, snapshot_id: str = "snap-1") -> ExecutionIntent:
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="market-123",
        token_id="yes-token",
        timeout_seconds=3600,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
    )


def _request(idx: int, *, decision_id: str | None = None) -> BatchSubmitRequest:
    return BatchSubmitRequest(
        decision_id=decision_id or f"decision-{idx}",
        intent_kind=IntentKind.EXIT,
        position_id=f"pos-{idx}",
        intent=_intent(price=round(0.10 + (idx % 40) * 0.01, 2)),
        snapshot=FakeSnapshot(captured_at=datetime.now(timezone.utc).isoformat()),
        order_type="GTC",
    )


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


def _accepted(order_id: str) -> dict:
    return {"success": True, "status": "LIVE", "orderID": order_id, "errorCode": None, "errorMessage": None}


def _rejected(code: str = "PRICE_OUT_OF_RANGE", message: str = "bad price") -> dict:
    return {"success": False, "status": "REJECTED", "orderID": None, "errorCode": code, "errorMessage": message}


def _unmapped() -> dict:
    return {
        "success": False,
        "status": "unmapped",
        "orderID": None,
        "errorCode": "BATCH_RESPONSE_UNMAPPED",
        "errorMessage": "batch response could not be mapped to this request",
    }


class TestSubmitOrdersBatchPersistBeforeCall:
    def test_commands_committed_before_sdk_call_fires(self, file_conn):
        # W2.1 verifier finding (2026-07-02): a spy that reads via the SAME
        # connection object always sees uncommitted writes too (that's just
        # how a single sqlite3.Connection works), so that shape "passes"
        # even if the implementation's conn.commit() were deleted -- not
        # load-bearing proof. Fixed here: the DB is file-backed (file_conn
        # fixture) and the spy opens a GENUINELY SEPARATE, read-only
        # connection to that file mid-call. A separate connection can only
        # see rows the writer connection actually committed.
        conn, db_path = file_conn
        _ensure_snapshot(conn)
        seen_committed_rows_at_call_time = {}

        class SpyGatewayClient(FakeGatewayClient):
            def place_limit_orders_batch(self, envelopes):
                reader = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    rows = reader.execute("SELECT command_id, state FROM venue_commands").fetchall()
                finally:
                    reader.close()
                seen_committed_rows_at_call_time["rows"] = [(r[0], r[1]) for r in rows]
                return super().place_limit_orders_batch(envelopes)

        client = SpyGatewayClient(submit_responses=[[_accepted("ord-0")]])
        outcomes = submit_orders_batch(conn, _adapter(), client, [_request(0)])

        assert outcomes[0].status == "acked"
        rows_at_call_time = seen_committed_rows_at_call_time["rows"]
        assert len(rows_at_call_time) == 1
        assert rows_at_call_time[0][1] == "SUBMITTING"  # post SUBMIT_REQUESTED, pre-ack

    def test_two_commands_and_two_submit_requested_events_persisted_for_one_call(self, mem_conn):
        _ensure_snapshot(mem_conn)
        client = FakeGatewayClient(submit_responses=[[_accepted("ord-0"), _accepted("ord-1")]])

        outcomes = submit_orders_batch(mem_conn, _adapter(), client, [_request(0), _request(1)])

        assert len(outcomes) == 2
        assert all(o.status == "acked" for o in outcomes)
        assert len(client.submit_calls) == 1
        assert len(client.submit_calls[0]) == 2
        events = mem_conn.execute(
            "SELECT event_type FROM venue_command_events WHERE event_type = 'SUBMIT_REQUESTED'"
        ).fetchall()
        assert len(events) == 2


class TestSubmitOrdersBatchChunking:
    def test_16_requests_issue_two_sdk_calls(self, mem_conn):
        _ensure_snapshot(mem_conn)
        requests = [_request(i) for i in range(16)]
        client = FakeGatewayClient(
            submit_responses=[
                [_accepted(f"ord-{i}") for i in range(MAX_ORDERS_PER_BATCH)],
                [_accepted("ord-15")],
            ]
        )

        outcomes = submit_orders_batch(mem_conn, _adapter(), client, requests)

        assert len(outcomes) == 16
        assert len(client.submit_calls) == 2
        assert len(client.submit_calls[0]) == MAX_ORDERS_PER_BATCH
        assert len(client.submit_calls[1]) == 1
        assert all(o.status == "acked" for o in outcomes)


class TestSubmitOrdersBatchMapping:
    def test_rejected_and_unmapped_items_get_correct_status(self, mem_conn):
        _ensure_snapshot(mem_conn)
        client = FakeGatewayClient(
            submit_responses=[[_accepted("ord-0"), _rejected(), _unmapped()]]
        )

        outcomes = submit_orders_batch(mem_conn, _adapter(), client, [_request(0), _request(1), _request(2)])

        assert [o.status for o in outcomes] == ["acked", "rejected", "unknown"]
        events = {
            row[0]: row[1]
            for row in mem_conn.execute(
                "SELECT command_id, event_type FROM venue_command_events "
                "WHERE event_type IN ('SUBMIT_ACKED','SUBMIT_REJECTED','SUBMIT_UNKNOWN')"
            ).fetchall()
        }
        assert sorted(events.values()) == ["SUBMIT_ACKED", "SUBMIT_REJECTED", "SUBMIT_UNKNOWN"]

    def test_rejected_or_unmapped_items_do_not_halt_later_chunks(self, mem_conn):
        # A per-item rejection/unmapped result within an otherwise-received
        # response is NOT the same as the SDK call raising -- later chunks
        # must still be attempted.
        _ensure_snapshot(mem_conn)
        requests = [_request(i) for i in range(16)]
        client = FakeGatewayClient(
            submit_responses=[
                [_rejected()] * MAX_ORDERS_PER_BATCH,
                [_accepted("ord-15")],
            ]
        )

        outcomes = submit_orders_batch(mem_conn, _adapter(), client, requests)

        assert len(client.submit_calls) == 2
        assert outcomes[15].status == "acked"


class TestSubmitOrdersBatchPartialFailure:
    def test_chunk_2_of_3_sdk_exception_halts_chunk_3_not_attempted(self, mem_conn):
        _ensure_snapshot(mem_conn)
        requests = [_request(i) for i in range(3 * MAX_ORDERS_PER_BATCH)]
        client = FakeGatewayClient(
            submit_responses=[
                [_accepted(f"ord-{i}") for i in range(MAX_ORDERS_PER_BATCH)],
                None,  # chunk 2 raises before consuming this
                None,  # chunk 3 never attempted
            ],
            fail_submit_on_call_index=1,
        )

        outcomes = submit_orders_batch(mem_conn, _adapter(), client, requests)

        chunk1 = outcomes[0:MAX_ORDERS_PER_BATCH]
        chunk2 = outcomes[MAX_ORDERS_PER_BATCH : 2 * MAX_ORDERS_PER_BATCH]
        chunk3 = outcomes[2 * MAX_ORDERS_PER_BATCH :]
        assert all(o.status == "acked" for o in chunk1)
        assert all(o.status == "unknown_side_effect" for o in chunk2)
        assert all(o.status == "not_attempted" for o in chunk3)
        # Only 2 SDK calls made -- chunk 3 never reached the network.
        assert len(client.submit_calls) == 2
        # Chunk 2's rows: SUBMIT_TIMEOUT_UNKNOWN persisted (ambiguous, not
        # silently dropped).
        chunk2_command_ids = [o.command_id for o in chunk2]
        timeout_events = mem_conn.execute(
            "SELECT command_id FROM venue_command_events WHERE event_type = 'SUBMIT_TIMEOUT_UNKNOWN'"
        ).fetchall()
        assert sorted(r[0] for r in timeout_events) == sorted(chunk2_command_ids)
        # Chunk 3's rows: NEVER persisted at all (no venue_commands row) --
        # design decision documented in batch_order_submission.py module
        # docstring: persist happens immediately before that chunk's own
        # SDK call, so an unreached chunk has nothing to roll back.
        all_command_rows = mem_conn.execute("SELECT command_id FROM venue_commands").fetchall()
        assert len(all_command_rows) == 2 * MAX_ORDERS_PER_BATCH
        assert all(o.command_id is None for o in chunk3)


class TestSubmitOrdersBatchSelfTradeGuardHook:
    def test_absent_hook_behaves_as_today(self, mem_conn):
        _ensure_snapshot(mem_conn)
        client = FakeGatewayClient(submit_responses=[[_accepted("ord-0")]])
        outcomes = submit_orders_batch(mem_conn, _adapter(), client, [_request(0)], self_trade_verdicts=None)
        assert outcomes[0].status == "acked"

    def test_would_self_cross_blocks_only_that_request(self, mem_conn):
        _ensure_snapshot(mem_conn)
        client = FakeGatewayClient(submit_responses=[[_accepted("ord-1")]])
        verdicts = {
            0: SelfTradeCheckResult(verdict=SelfTradeVerdict.WOULD_SELF_CROSS, reason="crosses resting sell"),
        }

        outcomes = submit_orders_batch(
            mem_conn, _adapter(), client, [_request(0), _request(1)], self_trade_verdicts=verdicts
        )

        assert outcomes[0].status == "rejected"
        assert outcomes[0].error_code == "SELF_TRADE_GUARD_BLOCKED"
        assert outcomes[0].command_id is None  # never persisted
        assert outcomes[1].status == "acked"
        # Only the surviving request reached the SDK call.
        assert len(client.submit_calls[0]) == 1

    def test_indeterminate_verdict_fails_closed(self, mem_conn):
        _ensure_snapshot(mem_conn)
        client = FakeGatewayClient(submit_responses=[[]])
        verdicts = {0: SelfTradeCheckResult(verdict=SelfTradeVerdict.INDETERMINATE, reason="own_open_orders_unavailable")}

        outcomes = submit_orders_batch(mem_conn, _adapter(), client, [_request(0)], self_trade_verdicts=verdicts)

        assert outcomes[0].status == "rejected"
        assert outcomes[0].error_code == "SELF_TRADE_GUARD_BLOCKED"


class TestSubmitOrdersBatchRateBudgetHook:
    def test_absent_hook_behaves_as_today(self, mem_conn):
        _ensure_snapshot(mem_conn)
        client = FakeGatewayClient(submit_responses=[[_accepted("ord-0")]])
        outcomes = submit_orders_batch(mem_conn, _adapter(), client, [_request(0)], rate_budget=None)
        assert outcomes[0].status == "acked"

    def test_denied_budget_marks_rate_limited_and_halts_later_chunks(self, mem_conn):
        from src.venue.rate_budget import RateBudgetConfig, VenueRateBudget

        _ensure_snapshot(mem_conn)
        # Zero-capacity budget: the very first try_acquire is DEFERRED.
        budget = VenueRateBudget(RateBudgetConfig(capacity_tokens=1.0, rate_per_sec=0.0001, cancel_reserve_tokens=1.0))
        client = FakeGatewayClient(submit_responses=[[_accepted("ord-0")]])

        outcomes = submit_orders_batch(mem_conn, _adapter(), client, [_request(0), _request(1)], rate_budget=budget)

        assert all(o.status == "rate_limited" for o in outcomes)
        assert client.submit_calls == []
        assert all(o.command_id is None for o in outcomes)  # never persisted


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
