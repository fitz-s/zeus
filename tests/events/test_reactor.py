# Created: 2026-05-24
# Last reused/audited: 2026-08-13
# Lifecycle: created=2026-05-24; last_reviewed=2026-08-13; last_reused=2026-08-13
# Authority basis: EDLI v1 implementation prompt §13 event reactor no-bypass contract.
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.compiler import AuthorityEvidence, EvidenceClock, PreSubmitProofBundle
from src.decision_kernel.verifier import ENSEMBLE_MEMBERS_JSON_SOURCE
from src.events.event_store import (
    EventStore,
    GLOBAL_WINNER_SUBMIT_FENCED,
    GLOBAL_WINNER_TARGETED_CLAIM,
)
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    MarketBookEventPayload,
    make_day0_extreme_updated_event,
    make_opportunity_event,
)
from src.events.reactor import (
    EventSubmissionReceipt,
    GlobalBatchSubmitResult,
    GlobalHeldSellCompletionCut,
    OpportunityEventReactor,
    ReactorConfig,
    ReactorResult,
    TERMINAL_MONEY_PATH_REASONS,
    TRANSIENT_MONEY_PATH_REASONS,
    _EXECUTABLE_SNAPSHOT_RETRY,
    _POST_SUBMIT_WORLD_WRITE_LOCK_RETRY,
    _build_day0_posterior_redecision_events,
    _edli_emit_day0_extreme_events,
    _held_position_monitor_preemption_pending,
    _is_posterior_staleness_reason,
    _process_pending_cancelled,
    _rank_forecast_wake_events,
    _is_transient_money_path_reason,
)
from src.state.db import init_schema, world_write_mutex
from src.sizing.portfolio_reservation import PortfolioReservationLedger
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger


@pytest.mark.parametrize("post_only,expected_order_type", [(False, "FOK"), (True, "GTC")])
def test_global_sealed_provider_recaptures_selected_book_after_slow_gates(
    monkeypatch, post_only, expected_order_type
):
    import json

    from src.data.market_scanner import _sha256_json
    from src.engine.event_reactor_adapter import SealedBookOverride
    from src.events import reactor as reactor_module

    gate_calls = []
    balance_payloads = []
    monkeypatch.setattr(
        reactor_module,
        "_edli_heartbeat_authority_summary",
        lambda order_type: gate_calls.append(("heartbeat", order_type))
        or {"allow_submit": True, "authority_id": "hb"},
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_user_ws_authority_summary",
        lambda checked_at: gate_calls.append(("ws", checked_at))
        or {"allow_submit": True, "authority_id": "ws"},
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_venue_connectivity_authority_summary",
        lambda checked_at: gate_calls.append(("venue", checked_at))
        or {"allow_submit": True, "authority_id": "venue"},
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_canonical_buy_collateral_payload",
        lambda *_args, **_kwargs: {"allow_submit": True},
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_balance_allowance_status",
        lambda final_intent, *_args, **_kwargs: (
            balance_payloads.append(dict(final_intent.payload))
            or ("OK", "balance", datetime.now(timezone.utc).isoformat())
        ),
    )
    book = {
        "asset_id": "yes-sealed",
        "asks": [{"price": "0.50", "size": "10"}],
        "bids": [{"price": "0.49", "size": "10"}],
    }
    book_hash = _sha256_json(book)
    sealed_now = datetime.now(timezone.utc) - timedelta(seconds=3)
    override = SealedBookOverride(
        token_id="yes-sealed",
        side="BUY",
        snapshot_id="snap-sealed",
        raw_orderbook_hash=book_hash,
        orderbook_depth_jsonb=json.dumps(book, sort_keys=True, separators=(",", ":")),
        best_bid=0.49,
        best_ask=0.50,
        tick_size=0.01,
        min_order_size=1.0,
        neg_risk=False,
        captured_at=sealed_now.isoformat(),
        freshness_deadline=(sealed_now + timedelta(seconds=60)).isoformat(),
        curve_ttl_seconds=60.0,
    )
    live_seen_at = datetime.now(timezone.utc)
    live_book = {
        **book,
        "hash": "live-selected-book-hash",
    }
    book_fetches = []
    provider = reactor_module._edli_pre_submit_authority_provider_from_book_evidence_conn(
        None,
        {"pre_submit_max_quote_age_ms": 1000},
        book_quote_provider=lambda token: (
            book_fetches.append(token) or live_book,
            live_seen_at,
            "clob_jit_book",
        ),
    )
    intent = SimpleNamespace(
        payload={
            "token_id": "yes-sealed",
            "side": "BUY",
            "limit_price": 0.51,
            "size": 2.0,
            "post_only": post_only,
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
            "notional_usd": 1.02,
        }
    )
    snapshot = SimpleNamespace(payload={"identity": "snap-sealed"})
    witness = provider(
        intent,
        snapshot,
        datetime(2026, 8, 9, 10, 0, 30, tzinfo=timezone.utc),
        sealed_book_override=override,
    )
    assert witness.book_hash == "live-selected-book-hash"
    assert witness.current_best_bid == 0.49
    assert witness.current_best_ask == 0.50
    assert datetime.fromisoformat(witness.book_captured_at) == live_seen_at
    assert datetime.fromisoformat(witness.book_captured_at) > sealed_now
    assert book_fetches == ["yes-sealed"]
    assert witness.heartbeat_status == "OK"
    assert witness.user_ws_status == "OK"
    assert witness.venue_connectivity_status == "OK"
    assert witness.balance_allowance_status == "OK"
    assert gate_calls[0][1] == expected_order_type
    assert sum(kind == "heartbeat" for kind, _value in gate_calls) == 1
    assert sum(kind == "ws" for kind, _value in gate_calls) == 1
    assert sum(kind == "venue" for kind, _value in gate_calls) == 1
    gate_timestamp = gate_calls[1][1].isoformat()
    assert witness.heartbeat_checked_at == gate_timestamp
    assert witness.user_ws_checked_at == gate_timestamp
    assert witness.venue_connectivity_checked_at == gate_timestamp
    assert datetime.fromisoformat(witness.checked_at) >= gate_calls[1][1]
    assert balance_payloads[0]["size"] == 2.0
    assert balance_payloads[0]["side"] == "BUY"
    assert balance_payloads[0]["limit_price"] == 0.51
    assert balance_payloads[0]["notional_usd"] > 0
    assert balance_payloads[0]["post_only"] is post_only
    if not post_only:
        live_book["asks"] = [{"price": "0.52", "size": "10"}]
        live_book["hash"] = "adverse-live-book-hash"
        with pytest.raises(ValueError, match="PRE_SUBMIT_BOOK_AUTHORITY_JIT_DEPTH_INSUFFICIENT"):
            provider(
                intent,
                snapshot,
                datetime.now(timezone.utc),
                sealed_book_override=override,
            )
        live_book["asks"] = [{"price": "0.50", "size": "10"}]
        live_book["hash"] = "live-selected-book-hash"
    for field, bad_value in (
        ("snapshot_id", "wrong-snapshot"),
        ("raw_orderbook_hash", "wrong-hash"),
        ("curve_ttl_seconds", 0.0),
        ("best_ask", 0.51),
        ("tick_size", 0.02),
        ("min_order_size", 2.0),
        ("neg_risk", True),
    ):
        with pytest.raises(ValueError):
            provider(
                intent,
                snapshot,
                datetime.now(timezone.utc),
                sealed_book_override=replace(override, **{field: bad_value}),
            )
    monkeypatch.setattr(
        reactor_module,
        "_edli_heartbeat_authority_summary",
        lambda _order_type: {"allow_submit": False, "authority_id": "hb"},
    )
    with pytest.raises(ValueError, match="PRE_SUBMIT_HEARTBEAT_ORDER_TYPE_BLOCKED"):
        provider(
            intent,
            snapshot,
            datetime.now(timezone.utc),
            sealed_book_override=override,
        )


def test_global_builder_uses_sealed_book_and_one_final_provider_call(monkeypatch):
    """The production builder must not manufacture a second complete provisional witness."""

    from src.engine import event_reactor_adapter as era
    from src.engine.event_reactor_adapter import (
        PreSubmitAuthorityWitness,
        SealedBookObservation,
    )

    decision_time = datetime(2026, 8, 9, 10, 0, 30, tzinfo=timezone.utc)
    captured_at = decision_time
    depth = json.dumps(
        {
            "asset_id": "yes-sealed",
            "asks": [{"price": "0.50", "size": "10"}],
            "bids": [{"price": "0.49", "size": "10"}],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot = SimpleNamespace(
        snapshot_id="snap-sealed",
        selected_outcome_token_id="yes-sealed",
        raw_orderbook_hash=hashlib.sha256(depth.encode()).hexdigest(),
        orderbook_depth_jsonb=depth,
        orderbook_top_bid=0.49,
        orderbook_top_ask=0.50,
        min_tick_size=0.01,
        min_order_size=1.0,
        neg_risk=False,
        captured_at=captured_at,
        freshness_deadline=captured_at + timedelta(seconds=60),
    )
    curve = SimpleNamespace(quote_ttl=timedelta(seconds=60), fee_model=SimpleNamespace(fee_rate=0.01))
    candidate = SimpleNamespace(
        book_snapshot_id="snap-sealed",
        executable_cost_curve=curve,
    )
    global_decision = SimpleNamespace(
        candidate=candidate,
        limit_price=0.51,
        shares=2.0,
        cost_usd=1.02,
        expected_fill_price_before_fee=0.50,
    )
    receipt = SimpleNamespace(
        global_actuation=SimpleNamespace(decision=global_decision, winner_event_id="evt-sealed"),
        strategy_key="Center Bin Buy",
        direction="buy_yes",
        metric="high",
        decision_proof_bundle=None,
        day0_probability_authority=None,
        qkernel_execution_economics={},
        event_id="evt-sealed",
    )
    event = SimpleNamespace(event_id="evt-sealed", event_type="FORECAST_DECISION")
    executable = SimpleNamespace(
        payload={
            "identity": "snap-sealed",
            "selected_snapshot_id": "snap-sealed",
            "token_id": "yes-sealed",
            "condition_id": "cond-sealed",
            "min_tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
        }
    )
    quote = SimpleNamespace(payload={"best_bid": 0.49, "best_ask": 0.50})
    forecast = SimpleNamespace(payload={})
    cost = SimpleNamespace(payload={})
    live_cap = SimpleNamespace(payload={"usage_id": "usage-sealed", "reserved_notional_usd": 1.02})
    actionable_payload = {
        "event_id": "evt-sealed",
        "event_type": "FORECAST_DECISION",
        "condition_id": "cond-sealed",
        "token_id": "yes-sealed",
        "direction": "buy_yes",
        "executable_snapshot_id": "snap-sealed",
        "q_source": "replacement_0_1",
        "_edli_q_source": "replacement_0_1",
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {"cost": 0.50},
        "proof_execution_mode_intent": "TAKER",
        "rest_then_cross_policy": "TAKER",
        "strategy_key": "Center Bin Buy",
        "family_id": "family-sealed",
        "city": "Chicago",
        "target_date": "2026-08-09",
        "metric": "high",
        "live_cap_reserved_notional_usd": 1.02,
    }
    final_payload = {
        **actionable_payload,
        "final_intent_id": "intent-sealed",
        "side": "BUY",
        "limit_price": 0.51,
        "size": 2.0,
        "notional_usd": 1.02,
        "post_only": False,
        "tick_size": 0.01,
        "min_order_size": 1.0,
        "neg_risk": False,
        "order_mode": "TAKER",
        "order_type": "FOK_LIMIT",
        "time_in_force": "FOK",
    }
    base_certs = (forecast, executable, quote, cost)
    compile_result = SimpleNamespace(status="VERIFIED", certificates=base_certs, failures=())
    monkeypatch.setattr(era.DecisionCompiler, "compile_authority_graph", lambda *_a, **_k: compile_result)
    monkeypatch.setattr(era, "_payload", lambda _event: {})
    monkeypatch.setattr(era, "_assert_event_bound_strategy_live_admitted", lambda **_k: None)
    monkeypatch.setattr(era, "_assert_event_bound_receipt_live_authority", lambda _r: None)
    monkeypatch.setattr(era, "_assert_event_bound_calibration_live_admitted", lambda _c: None)
    monkeypatch.setattr(era, "_day0_live_source_parent_certificates", lambda **_k: ())
    monkeypatch.setattr(era, "_required_cert", lambda _certs, claim: {
        era.claims.EXECUTABLE_SNAPSHOT: executable,
        era.claims.QUOTE_FEASIBILITY: quote,
        era.claims.COST_MODEL: cost,
        era.claims.FORECAST_AUTHORITY: forecast,
        era.claims.CALIBRATION: forecast,
    }[claim])
    monkeypatch.setattr(era, "_build_live_cap_certificate_from_ledger", lambda **_k: live_cap)
    monkeypatch.setattr(era, "_actionable_payload_from_receipt", lambda *_a, **_k: dict(actionable_payload))
    monkeypatch.setattr(era, "_assert_live_entry_submit_authority", lambda _p: None)
    monkeypatch.setattr(era, "build_actionable_trade_certificate", lambda **_k: SimpleNamespace(payload=dict(actionable_payload)))
    monkeypatch.setattr(era, "_global_jit_book_hash_for_submit", lambda **_k: None)
    monkeypatch.setattr(era, "_fresh_rest_then_cross_mode", lambda **_k: "TAKER")
    monkeypatch.setattr(era, "_current_maker_fill_authority_rejection_reason", lambda **_k: None)
    monkeypatch.setattr(era, "_validate_final_order_mode_or_abort", lambda **_k: "TAKER")
    provisional_types = []
    monkeypatch.setattr(
        era,
        "_day0_live_submit_admission_rejection_reason",
        lambda **kwargs: provisional_types.append(type(kwargs["authority_witness"])) or None,
    )
    monkeypatch.setattr(era, "_passive_maker_context_and_book", lambda **_k: (None, 0.49, 0.50))
    monkeypatch.setattr(era, "_executable_market_context_from_snapshot", lambda _s: {})
    monkeypatch.setattr(era, "_build_event_bound_taker_quality_proof", lambda **_k: {"passed": True})
    final_intent_builds = []
    monkeypatch.setattr(
        era,
        "build_final_intent_certificate_from_actionable",
        lambda **kwargs: (
            final_intent_builds.append(kwargs)
            or SimpleNamespace(payload=dict(final_payload))
        ),
    )
    witness = PreSubmitAuthorityWitness(
        quote_seen_at=captured_at.isoformat(),
        book_hash=snapshot.raw_orderbook_hash,
        current_best_bid=0.49,
        current_best_ask=0.50,
        tick_size=0.01,
        min_order_size=1.0,
        neg_risk=False,
        heartbeat_status="OK",
        user_ws_status="OK",
        venue_connectivity_status="OK",
        balance_allowance_status="OK",
        book_authority_id="clob_jit_book",
        book_captured_at=captured_at.isoformat(),
        heartbeat_authority_id="hb",
        heartbeat_checked_at=captured_at.isoformat(),
        user_ws_authority_id="ws",
        user_ws_checked_at=captured_at.isoformat(),
        venue_connectivity_authority_id="venue",
        venue_connectivity_checked_at=captured_at.isoformat(),
        balance_allowance_authority_id="balance",
        balance_allowance_checked_at=captured_at.isoformat(),
        orderbook_depth_jsonb=depth,
    )
    provider_calls = []
    persisted_snapshot_bases = []

    def spy_provider(final_intent, _snapshot, _decision_time, *, sealed_book_override=None):
        provider_calls.append((dict(final_intent.payload), sealed_book_override))
        return witness

    monkeypatch.setattr(
        era,
        "persist_presubmit_jit_snapshot",
        lambda _conn, elected_snapshot, **_kwargs: persisted_snapshot_bases.append(
            elected_snapshot
        ),
    )
    monkeypatch.setattr(era, "validate_final_intent_cert_for_existing_executor", lambda _c: "native-hash")
    monkeypatch.setattr(era, "_release_live_order_build_stale_transaction", lambda *_a, **_k: None)
    monkeypatch.setattr(era, "_entry_global_submit_suppression_reason", lambda: None)
    monkeypatch.setattr(era, "_locked_live_opportunity_active_order_reason", lambda *_a, **_k: None)
    monkeypatch.setattr(era, "_run_live_order_build_savepoint", lambda *_a, **_k: (SimpleNamespace(), SimpleNamespace(), SimpleNamespace()))
    handoff = SimpleNamespace(authority=SimpleNamespace(snapshot=snapshot), candidate=candidate)

    live_cap_conn = sqlite3.connect(":memory:")
    trade_conn = sqlite3.connect(":memory:")
    result = era._build_live_execution_command_certificates(
        event=event,
        receipt=receipt,
        decision_time=decision_time,
        live_cap_conn=live_cap_conn,
        trade_conn=trade_conn,
        pre_submit_authority_provider=spy_provider,
        live_order_schema_initialized=True,
        global_jit_handoff=handoff,
    )

    assert result
    assert provisional_types == [SealedBookObservation]
    assert len(provider_calls) == 1
    final_intent_payload, sealed_override = provider_calls[0]
    assert final_intent_payload["size"] == 2.0
    assert final_intent_payload["side"] == "BUY"
    assert final_intent_payload["limit_price"] == 0.51
    assert final_intent_payload["notional_usd"] == 1.02
    assert final_intent_payload["post_only"] is False
    assert final_intent_builds[0]["order_type"] == "FOK_LIMIT"
    assert final_intent_builds[0]["time_in_force"] == "FOK"
    assert sealed_override is not None
    assert sealed_override.snapshot_id == "snap-sealed"
    assert persisted_snapshot_bases == [snapshot]


def _store() -> tuple[sqlite3.Connection, EventStore]:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, EventStore(conn)


def _held_sell_completion_result(
    *,
    position_id: str,
    token_id: str,
    probability_content_identity: str,
    outcome: str = "ACTUATED",
    terminal_no_trade_reason: str = "",
) -> ReactorResult:
    coverage = SimpleNamespace(
        position_id=position_id,
        token_id=token_id,
        status="EVALUATED",
        candidate_id=f"candidate:{position_id}",
        probability_content_identity=probability_content_identity,
        selection_epoch_identity=f"epoch:{position_id}",
        sell_book_witness_identity=f"book:{position_id}",
    )
    return ReactorResult(
        global_held_sell_completion_cuts=[
            GlobalHeldSellCompletionCut(
                holding_coverage=(coverage,),
                economic_cut_completed=outcome != "INCOMPLETE",
                outcome=outcome,
                selected_position_id=(position_id if outcome == "ACTUATED" else None),
                selected_token_id=(token_id if outcome == "ACTUATED" else None),
                selected_candidate_id=(
                    f"candidate:{position_id}" if outcome == "ACTUATED" else None
                ),
                terminal_no_trade_reason=terminal_no_trade_reason,
            )
        ]
    )


@pytest.mark.parametrize(
    (
        "wake_kind",
        "completion_due",
        "exact_held_completion",
        "entries_blocked",
        "has_held_exposure",
        "expected_reduce_only",
        "expected_terminal_without_cut",
    ),
    (
        ("generic_no_exposure", True, False, False, False, False, False),
        ("generic_held", True, False, False, True, False, False),
        ("blocked_generic_no_exposure", True, False, True, False, False, True),
        ("blocked_generic_held", True, False, True, True, True, False),
        ("exact_terminal_no_exposure", True, True, True, False, False, False),
        ("exact_active_exposure", True, True, True, True, True, False),
        ("ordinary_probability", False, False, True, True, False, False),
    ),
)
def test_completion_mode_separates_fairness_from_reduce_only(
    wake_kind,
    completion_due,
    exact_held_completion,
    entries_blocked,
    has_held_exposure,
    expected_reduce_only,
    expected_terminal_without_cut,
):
    """Completion debt must not remove BUYs after its held capital is gone."""
    from src.events.reactor import _global_auction_completion_mode

    class _Rows:
        def fetchone(self):
            return (1,) if has_held_exposure else None

    class _TradeConnection:
        def execute(self, statement):
            assert "FROM position_current" in statement
            return _Rows()

    assert wake_kind
    mode = _global_auction_completion_mode(
        completion_due=completion_due,
        exact_held_completion=exact_held_completion,
        entries_blocked=entries_blocked,
        trade_conn=_TradeConnection(),
    )
    assert mode.fairness_reserved is completion_due
    assert mode.reduce_only is expected_reduce_only
    assert mode.terminal_without_cut is expected_terminal_without_cut


def test_exact_completion_exposure_read_failure_stays_reduce_only(caplog):
    from src.events.reactor import _global_auction_completion_mode

    class _TradeConnection:
        def execute(self, _statement):
            raise sqlite3.OperationalError("database is busy")

    with caplog.at_level(logging.WARNING):
        mode = _global_auction_completion_mode(
            completion_due=True,
            exact_held_completion=True,
            trade_conn=_TradeConnection(),
        )

    assert mode.fairness_reserved is True
    assert mode.reduce_only is True
    assert "retaining reduce-only scope" in caplog.text


@pytest.mark.parametrize(
    ("provider", "expected"),
    (
        (lambda: frozenset(), False),
        (lambda: frozenset({("Dallas", "2026-08-12", "high")}), True),
        (lambda: None, True),
        (None, True),
    ),
)
def test_paused_forecast_carrier_runs_held_auction_only_when_exposure_exists_or_unknown(
    provider,
    expected,
):
    from src.events.reactor import (
        _paused_forecast_carrier_requires_held_auction,
    )

    assert _paused_forecast_carrier_requires_held_auction(provider) is expected


def test_paused_forecast_carrier_held_read_failure_retains_reduce_only_auction(caplog):
    from src.events.reactor import (
        _paused_forecast_carrier_requires_held_auction,
    )

    def unreadable():
        raise sqlite3.OperationalError("database is busy")

    with caplog.at_level(logging.WARNING):
        assert _paused_forecast_carrier_requires_held_auction(unreadable) is True

    assert "retaining reduce-only auction" in caplog.text


def test_paused_forecast_held_auction_is_wired_through_reduce_only_completion_cut():
    from src.events.reactor import run_edli_event_reactor_cycle

    source = inspect.getsource(run_edli_event_reactor_cycle)
    materialized = source.index(
        "_paused_forecast_carrier_requires_held_auction("
    )
    completion_mode = source.index("_global_auction_completion_mode(", materialized)
    process_pending = source.index("reactor.process_pending(", completion_mode)

    assert "or paused_forecast_held_auction" in source[materialized:process_pending]
    assert (
        "selection_completion_reserved=(\n"
        "                _monitor_completion_mode.reduce_only"
    ) in source[completion_mode:process_pending]
    assert materialized < completion_mode < process_pending


def test_durable_exact_completion_debt_gets_one_bounded_fairness_turn(monkeypatch):
    from src.events import reactor
    from src.runtime import reactor_wake

    queued = {"held-debt"}
    monkeypatch.setattr(
        reactor_wake,
        "exact_held_sell_completion_wake_ids",
        lambda **_kwargs: frozenset(queued),
    )
    reactor._DURABLE_EXACT_HELD_COMPLETION_SEEN.clear()
    try:
        assert reactor._claim_durable_exact_held_sell_completion_turn() is True
        assert reactor._claim_durable_exact_held_sell_completion_turn() is False
        queued.add("new-generation")
        assert reactor._claim_durable_exact_held_sell_completion_turn() is True
    finally:
        reactor._DURABLE_EXACT_HELD_COMPLETION_SEEN.clear()


def test_durable_fallback_cut_binds_current_exact_request(monkeypatch):
    from src.events import reactor
    from src.runtime import reactor_wake

    request = reactor_wake.make_held_sell_reauction_request(
        position_id="durable-fallback-held",
        family=("Guangzhou", "2026-08-09", "high"),
        probability_content_identity="q-durable-fallback",
        probability_observed_at="2026-08-09T10:32:20+00:00",
        held_token_id="held-token-durable-fallback",
        held_best_bid=0.001,
        bid_observed_at="2026-08-09T10:32:20+00:00",
        schema_version=4,
        book_state="NO_EXECUTABLE_BOOK",
    )
    wake = SimpleNamespace(held_sell_reauction_requests=(request,))
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_for_reason",
        lambda *_args, **_kwargs: (wake,),
    )
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )

    durable_requests = reactor._durable_exact_held_sell_completion_requests()

    assert durable_requests == (request,)
    cut_requests = reactor._held_sell_completion_cut_requests(
        completion_wake=False,
        producer_requests=(),
        durable_turn_claimed=True,
        durable_requests=durable_requests,
    )
    assert cut_requests == (request,)
    assert reactor._held_sell_completion_cut_requests(
        completion_wake=False,
        producer_requests=(),
        durable_turn_claimed=False,
        durable_requests=durable_requests,
    ) == ()
    coverage = SimpleNamespace(
        position_id=request.position_id,
        token_id=request.held_token_id,
        status="EXCLUDED",
        book_state="NO_EXECUTABLE_BOOK",
        probability_content_identity="q-current-global-cut",
        selection_epoch_identity="epoch-current-global-cut",
        sell_book_witness_identity="book-current-global-cut",
    )
    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=cut_requests,
        result=ReactorResult(
            global_held_sell_completion_cuts=[
                GlobalHeldSellCompletionCut(
                    holding_coverage=(coverage,),
                    economic_cut_completed=False,
                    outcome="INCOMPLETE",
                )
            ]
        ),
    )
    assert len(receipts) == 1
    assert receipts[0].status == "NO_EXECUTABLE_BOOK"
    assert receipts[0].attempt_identity == request.attempt_identity


def test_completion_risk_bypass_is_bound_to_reduce_only_mode():
    from src.events.reactor import run_edli_event_reactor_cycle

    source = inspect.getsource(run_edli_event_reactor_cycle)

    assert source.count(
        "monitor_completion_reserved=completion_recovery_cycle"
    ) == 2
    assert (
        "_monitor_completion_mode.reduce_only or entry_risk_gate(event)"
        in source
    )
    assert (
        "_monitor_completion_mode.reduce_only\n"
        "                or get_current_level() == RiskLevel.GREEN"
        in source
    )
    assert "held_sell_completion_cycle or entry_risk_gate(event)" not in source
    completion_mode = source.index("_monitor_completion_mode = _construct_sql(")
    process_pending = source.index("reactor.process_pending(", completion_mode)
    assert "_monitor_completion_mode.terminal_without_cut" in source[
        completion_mode:process_pending
    ]
    assert "terminal_no_book_completion=True" in source[
        completion_mode:process_pending
    ]


def test_no_submit_claim_debt_drains_before_cycle_entry_gate():
    conn, store = _store()
    event = _forecast_event("claim-drain-before-gate")
    store.insert_or_ignore(event)
    claimed_at = "2026-05-24T18:30:00+00:00"
    assert store.claim(event.event_id, claimed_at=claimed_at)
    conn.commit()

    reactor = OpportunityEventReactor.__new__(OpportunityEventReactor)
    reactor._store = store
    reactor._cycle_entry_gate = lambda: False
    reactor._no_submit_claim_requeue_debt = {
        event.event_id: (claimed_at, 1)
    }

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.rejection_reasons == ["RISK_GUARD_BLOCKED"]
    row = conn.execute(
        """
        SELECT processing_status, claimed_at, last_error
          FROM opportunity_event_processing
         WHERE consumer_name = ? AND event_id = ?
        """,
        (store.consumer_name, event.event_id),
    ).fetchone()
    assert tuple(row) == (
        "pending",
        None,
        _POST_SUBMIT_WORLD_WRITE_LOCK_RETRY,
    )
    assert reactor._no_submit_claim_requeue_debt == {}


def test_paused_no_held_debt_wake_parks_before_claim_auction_or_receipt(tmp_path):
    from src.events.reactor import _paused_entry_wake_should_park
    from src.runtime import reactor_wake

    conn, store = _store()
    event = _forecast_event("paused-no-held")
    store.insert_or_ignore(event)
    wake_path = tmp_path / "wake.json"
    request = reactor_wake.make_held_sell_reauction_request(
        position_id="paused-no-held-position",
        family=("Dallas", "2026-05-24", "high"),
        probability_content_identity="q-paused-no-held",
        held_token_id="token-paused-no-held",
        held_best_bid=0.12,
        bid_observed_at="2026-05-24T17:59:00+00:00",
    )
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="paused-no-held-debt",
        forecast_families=(request.family,),
        held_sell_reauction_requests=(request,),
    )
    before = tuple(
        row[0]
        for row in conn.execute(
            "SELECT event_id FROM opportunity_event_processing "
            "WHERE processing_status = 'pending' ORDER BY event_id"
        )
    )
    decision_log_before = conn.execute(
        "SELECT COUNT(*) FROM decision_log"
    ).fetchone()[0]
    calls = {"source": 0, "auction": 0, "submit": 0}

    def submit(_event, _decision_time):
        calls["submit"] += 1
        return None

    def process_global_batch(*_args, **_kwargs):
        calls["auction"] += 1
        raise AssertionError("paused/no-held wake must not enter global auction")

    submit.process_global_batch = process_global_batch  # type: ignore[attr-defined]
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: calls.__setitem__("source", calls["source"] + 1) or True,
        executable_snapshot_gate=lambda *_args: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=submit,
        reject=lambda *_args: None,
        paused_entry_wake_gate=lambda: _paused_entry_wake_should_park(
            pause_reason="operator_pause",
            held_sell_reauction_requests=(request,),
            held_sell_request_exposure_provider=lambda: frozenset(),
        ),
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(conn),
    )

    result = reactor.process_pending(
        decision_time=datetime(2026, 5, 24, 18, 0, tzinfo=timezone.utc),
        limit=10,
    )

    after = tuple(
        row[0]
        for row in conn.execute(
            "SELECT event_id FROM opportunity_event_processing "
            "WHERE processing_status = 'pending' ORDER BY event_id"
        )
    )
    processing = conn.execute(
        "SELECT attempt_count, claimed_at FROM opportunity_event_processing "
        "WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert result.processed == result.rejected == result.retried == result.dead_lettered == 0
    assert result.proof_accepted == 0
    assert result.rejection_reasons == [
        "ENTRIES_PAUSED_NO_CANONICAL_HELD_FAMILIES"
    ]
    assert calls == {"source": 0, "auction": 0, "submit": 0}
    assert before == after == (event.event_id,)
    assert tuple(processing) == (0, None)
    assert conn.execute("SELECT COUNT(*) FROM edli_no_submit_receipts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0] == decision_log_before == 0
    assert reactor_wake.read_reactor_wake(path=wake_path) == wake
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=wake_path) == {
        wake.wake_id
    }


def test_paused_wake_resumes_same_pending_event_exactly_once():
    conn, store = _store()
    event = _forecast_event("paused-resume-exactly-once")
    store.insert_or_ignore(event)
    paused = [True]
    submitted: list[str] = []

    def submit(current_event, _decision_time):
        submitted.append(current_event.event_id)
        return None

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda *_args: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=submit,
        reject=lambda *_args: None,
        paused_entry_wake_gate=lambda: paused[0],
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(conn),
    )
    decision_time = datetime(2026, 5, 24, 18, 3, tzinfo=timezone.utc)

    parked = reactor.process_pending(decision_time=decision_time, limit=10)
    assert parked.retried == 0
    assert submitted == []
    paused[0] = False

    resumed = reactor.process_pending(decision_time=decision_time, limit=10)
    repeated = reactor.process_pending(decision_time=decision_time, limit=10)

    assert resumed.processed == 1
    assert repeated.processed == 0
    assert submitted == [event.event_id]
    row = conn.execute(
        "SELECT processing_status, attempt_count, claimed_at "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert tuple(row[:2]) == ("processed", 1)
    assert row[2] == decision_time.isoformat()


def test_risk_allocator_suppression_parks_ordinary_entry_work(monkeypatch):
    import src.engine.event_reactor_adapter as adapter
    import src.events.reactor as reactor_module

    conn, store = _store()
    event = _forecast_event("risk-suppressed-ordinary-entry")
    store.insert_or_ignore(event)
    risk_block: list[str | None] = [
        "RISK_ALLOCATOR_GLOBAL_ENTRY_UNAVAILABLE:"
        "reason=reduce_only_mode_active:reduce_only=True:"
        "kill_switch_reason=operator"
    ]
    monkeypatch.setattr(
        adapter,
        "_entry_pause_blocks_live_submit",
        lambda _conn: None,
    )
    monkeypatch.setattr(
        adapter,
        "_entry_global_submit_suppression_reason",
        lambda: risk_block[0],
    )
    submitted: list[str] = []
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda *_args: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda current, _decision_time: submitted.append(
            current.event_id
        ),
        reject=lambda *_args: None,
        paused_entry_wake_gate=lambda: reactor_module._paused_entry_wake_should_park(
            pause_reason=reactor_module._entry_reactor_park_reason(conn),
        ),
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(conn),
    )
    decision_time = datetime(2026, 5, 24, 18, 3, tzinfo=timezone.utc)

    parked = reactor.process_pending(decision_time=decision_time, limit=10)
    parked_row = conn.execute(
        "SELECT processing_status, attempt_count, claimed_at "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()

    assert parked.rejection_reasons == [
        "ENTRIES_PAUSED_NO_CANONICAL_HELD_FAMILIES"
    ]
    assert tuple(parked_row) == ("pending", 0, None)
    assert submitted == []

    risk_block[0] = None
    resumed = reactor.process_pending(decision_time=decision_time, limit=10)

    assert resumed.processed == 1
    assert submitted == [event.event_id]
    assert conn.execute(
        "SELECT processing_status, attempt_count FROM opportunity_event_processing "
        "WHERE event_id = ?",
        (event.event_id,),
    ).fetchone() == ("processed", 1)


def test_paused_entry_park_requires_exact_canonical_held_sell_work():
    from src.events.reactor import _paused_entry_wake_should_park
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        held_sell_request_exposure_provider=lambda: frozenset(),
    ) is True
    request = make_held_sell_reauction_request(
        position_id="held-position",
        family=("Dallas", "2026-05-24", "high"),
        probability_content_identity="q-held",
        held_token_id="held-token",
        held_best_bid=0.12,
        bid_observed_at="2026-05-24T17:59:00+00:00",
    )
    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        held_sell_reauction_requests=(request,),
        held_sell_request_exposure_provider=lambda: frozenset(
            {("other-position", request.family)}
        ),
    ) is True
    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        held_sell_reauction_requests=(request,),
        held_sell_request_exposure_provider=lambda: frozenset(
            {(request.position_id, request.family)}
        ),
    ) is False
    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        held_sell_request_exposure_provider=lambda: frozenset(),
        allow_forecast_carrier_progress=True,
    ) is False
    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        durable_exact_held_completion=True,
    ) is False
    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        allow_capital_proof_progress=True,
    ) is False
    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        monitor_completion_reserved=True,
    ) is False
    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        monitor_completion_reserved=False,
        held_sell_request_exposure_provider=lambda: frozenset(),
    ) is True


def test_paused_generic_completion_runs_cut_without_buy_and_clears_debt():
    """A paused BUY lane cannot strand an already-reserved global cut."""

    from types import SimpleNamespace

    from src.events import reactor as reactor_module

    conn, store = _store()
    event = _forecast_event(
        "paused-generic-completion",
        target_date="2026-05-25",
    )
    store.insert_or_ignore(event)
    calls = {"auction": 0, "buy": 0}

    def submit(*_args, **_kwargs):
        calls["buy"] += 1
        raise AssertionError("entry pause must forbid direct BUY submit")

    def process_global_batch(events, _decision_time, *, claim_unpaged_winner=None):
        calls["auction"] += 1
        assert claim_unpaged_winner is not None
        return GlobalBatchSubmitResult(
            receipts={
                current.event_id: EventSubmissionReceipt(
                    submitted=False,
                    event_id=current.event_id,
                    causal_snapshot_id=current.causal_snapshot_id,
                    reason="entries_paused:operator",
                    proof_accepted=False,
                )
                for current in events
            },
            winner_event_id=None,
            venue_submit_count=0,
            economic_cut_completed=True,
        )

    submit.process_global_batch = process_global_batch  # type: ignore[attr-defined]
    completion_reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda *_args: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=submit,
        reject=lambda *_args: None,
        paused_entry_wake_gate=lambda: reactor_module._paused_entry_wake_should_park(
            pause_reason="operator",
            monitor_completion_reserved=True,
        ),
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
    try:
        result = completion_reactor.process_pending(
            decision_time=_DT_VENUE_OPEN,
            limit=10,
        )
        assert calls == {"auction": 1, "buy": 0}, result
        assert result.global_auction_completed_non_cancelled == 1
        assert reactor_module._settle_global_auction_monitor_fairness(
            completion_due_at_start=True,
            result=SimpleNamespace(
                global_auction_completed_non_cancelled=(
                    result.global_auction_completed_non_cancelled
                ),
            ),
        )
        assert not reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set()
    finally:
        reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_paused_targeted_forecast_carrier_redecides_without_touching_ordinary_queue():
    """A pause fences BUY actuation, not the latest targeted FSR carrier."""

    conn, store = _store()
    carrier = _forecast_event("paused-latest-carrier")
    ordinary = _forecast_event("paused-ordinary-queue")
    store.insert_or_ignore(carrier)
    store.insert_or_ignore(ordinary)
    paused = [True]
    batch_identities: list[tuple[str, str]] = []
    venue_buy_commands: list[str] = []

    def submit(*_args, **_kwargs):
        venue_buy_commands.append("unexpected")
        raise AssertionError("paused carrier must not reach direct BUY submit")

    def process_global_batch(events, _decision_time, *, claim_unpaged_winner=None):
        assert claim_unpaged_winner is not None
        assert tuple(event.event_id for event in events) == (carrier.event_id,)
        batch_identities.extend(
            (event.event_id, event.causal_snapshot_id) for event in events
        )
        reason = (
            "entries_paused:operator"
            if paused[0]
            else "GLOBAL_AUCTION_NO_TRADE:NO_CURRENT_EXECUTABLE_POSITIVE_ORDER"
        )
        return GlobalBatchSubmitResult(
            receipts={
                event.event_id: EventSubmissionReceipt(
                    False,
                    event.event_id,
                    event.causal_snapshot_id,
                    reason=reason,
                    proof_accepted=False,
                )
                for event in events
            },
            winner_event_id=None,
            venue_submit_count=0,
            economic_cut_completed=not paused[0],
        )

    submit.process_global_batch = process_global_batch  # type: ignore[attr-defined]
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda *_args: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=submit,
        reject=lambda *_args: None,
        # This is the targeted forecast-posterior wake exception. The adapter's
        # entries_paused receipt remains the BUY actuation fence.
        paused_entry_wake_gate=lambda: False,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(conn),
    )
    decision_time = datetime(2026, 5, 24, 18, 3, tzinfo=timezone.utc)

    parked = reactor.process_pending(
        decision_time=decision_time,
        limit=10,
        targeted_event_ids=frozenset({carrier.event_id}),
        targeted_only=True,
    )

    assert parked.retried == 1
    assert venue_buy_commands == []
    parked_row = conn.execute(
        "SELECT processing_status, attempt_count, claimed_at FROM opportunity_event_processing WHERE event_id = ?",
        (carrier.event_id,),
    ).fetchone()
    assert tuple(parked_row[:2]) == ("pending", 1)
    retry_at = datetime.fromisoformat(parked_row[2])
    assert conn.execute(
        "SELECT processing_status, attempt_count FROM opportunity_event_processing WHERE event_id = ?",
        (ordinary.event_id,),
    ).fetchone() == ("pending", 0)

    paused[0] = False
    resumed = reactor.process_pending(
        decision_time=retry_at + timedelta(microseconds=1),
        limit=10,
        targeted_event_ids=frozenset({carrier.event_id}),
        targeted_only=True,
    )

    assert resumed.processed == 1
    assert batch_identities == [
        (carrier.event_id, carrier.causal_snapshot_id),
        (carrier.event_id, carrier.causal_snapshot_id),
    ]
    assert venue_buy_commands == []
    assert conn.execute(
        "SELECT processing_status, attempt_count FROM opportunity_event_processing WHERE event_id = ?",
        (carrier.event_id,),
    ).fetchone() == ("processed", 2)
    assert conn.execute(
        "SELECT processing_status, attempt_count FROM opportunity_event_processing WHERE event_id = ?",
        (ordinary.event_id,),
    ).fetchone() == ("pending", 0)


def test_targeted_forecast_wake_uses_carrier_exception_at_both_pause_gates():
    from src.events.reactor import run_edli_event_reactor_cycle

    source = inspect.getsource(run_edli_event_reactor_cycle)

    assert "forecast_posterior_wake" in source
    assert source.count("allow_forecast_carrier_progress=forecast_posterior_wake") == 2
    assert "forecast_posterior_wake and not targeted_event_ids" in source
    assert "forecast_posterior_wake or bool(targeted_event_ids)" in source
    assert "allow_paused_forecast_snapshot_completion" in source


def test_main_threads_pause_carrier_qualification_to_reactor(monkeypatch):
    import src.events.reactor as reactor_module
    import src.main as main

    captured: dict[str, object] = {}

    monkeypatch.setattr(main, "_start_edli_reactor_wake_listener", lambda: None)
    monkeypatch.setattr(
        main,
        "_edli_live_entry_readiness_block",
        lambda _cfg: (None, {}),
    )
    monkeypatch.setattr(
        main,
        "_held_position_monitor_entry_block_reason",
        lambda: None,
    )
    monkeypatch.setattr(
        main,
        "_unowned_day0_urgent_wake_pending",
        lambda: False,
    )
    main._capital_recovery_handoff_pending.clear()

    def run_cycle(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(reactor_module, "run_edli_event_reactor_cycle", run_cycle)

    assert main._edli_event_reactor_cycle(
        producer_wake_reason="forecast_posterior_advanced",
        allow_paused_forecast_snapshot_completion=True,
    ) is True
    assert captured["allow_paused_forecast_snapshot_completion"] is True
    assert captured["live_entry_block_reason"] == (
        "paused_forecast_snapshot_completion"
    )
    preemption_pending = captured["urgent_day0_pending"]
    assert callable(preemption_pending)
    assert preemption_pending() is False
    main._capital_recovery_handoff_pending.set()
    try:
        assert preemption_pending() is True
    finally:
        main._capital_recovery_handoff_pending.clear()


def test_main_drains_control_commands_before_edli_readiness(monkeypatch):
    import src.events.reactor as reactor_module
    import src.main as main
    from src.control import control_plane

    order: list[str] = []
    captured: dict[str, object] = {}
    bootstrap_complete = threading.Event()
    bootstrap_complete.set()
    monkeypatch.setattr(
        main,
        "_held_position_monitor_bootstrap_complete",
        bootstrap_complete,
    )

    monkeypatch.setattr(
        control_plane,
        "process_commands",
        lambda **_kwargs: order.append("control_drain"),
    )
    monkeypatch.setattr(
        main,
        "_start_edli_reactor_wake_listener",
        lambda: order.append("wake_listener"),
    )

    def readiness(_cfg):
        order.append("entry_readiness")
        return (None, {})

    monkeypatch.setattr(main, "_edli_live_entry_readiness_block", readiness)
    monkeypatch.setattr(
        main,
        "_held_position_monitor_entry_block_reason",
        lambda: None,
    )

    def run_cycle(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(reactor_module, "run_edli_event_reactor_cycle", run_cycle)

    assert main._edli_event_reactor_cycle() is True
    assert order == ["control_drain", "wake_listener", "entry_readiness"]
    assert captured["live_entry_block_reason"] is None


def test_main_control_drain_failure_blocks_entries_but_runs_reactor(monkeypatch):
    import src.events.reactor as reactor_module
    import src.main as main
    from src.control import control_plane

    captured: dict[str, object] = {}
    pauses: list[str] = []

    def fail_drain(**_kwargs):
        raise OSError("control queue unavailable")

    monkeypatch.setattr(control_plane, "process_commands", fail_drain)
    monkeypatch.setattr(
        control_plane,
        "pause_entries",
        lambda reason: pauses.append(reason),
    )
    monkeypatch.setattr(main, "_start_edli_reactor_wake_listener", lambda: None)
    monkeypatch.setattr(
        main,
        "_edli_live_entry_readiness_block",
        lambda _cfg: (None, {}),
    )
    monkeypatch.setattr(
        main,
        "_held_position_monitor_entry_block_reason",
        lambda: None,
    )

    def run_cycle(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(reactor_module, "run_edli_event_reactor_cycle", run_cycle)

    assert main._edli_event_reactor_cycle() is True
    assert captured["live_entry_block_reason"] == (
        "control_plane_command_drain_failed"
    )
    assert pauses == ["control_plane_command_drain_failed"]


def test_main_monitor_cadence_debt_blocks_buy_but_keeps_reactor_live(monkeypatch):
    import src.events.reactor as reactor_module
    import src.main as main

    captured: dict[str, object] = {}
    bootstrap_complete = threading.Event()
    bootstrap_complete.set()
    monkeypatch.setattr(
        main,
        "_held_position_monitor_bootstrap_complete",
        bootstrap_complete,
    )
    monkeypatch.setattr(main, "_start_edli_reactor_wake_listener", lambda: None)
    monkeypatch.setattr(
        main,
        "_edli_live_entry_readiness_block",
        lambda _cfg: (None, {}),
    )
    monkeypatch.setattr(
        main,
        "_held_position_monitor_entry_block_reason",
        lambda: "held_position_monitor_cadence_overdue",
    )
    canonical_debt = threading.Event()
    canonical_debt.set()
    monkeypatch.setattr(
        main,
        "_held_position_monitor_debt_pending",
        canonical_debt.is_set,
    )
    monkeypatch.setattr(
        reactor_module,
        "run_edli_event_reactor_cycle",
        lambda **kwargs: captured.update(kwargs) or True,
    )

    assert main._edli_event_reactor_cycle() is True
    assert captured["live_entry_block_reason"] == (
        "held_position_monitor_cadence_overdue"
    )
    monitor_pending = captured["held_position_monitor_pending"]
    assert callable(monitor_pending)
    assert monitor_pending() is False
    monitor_debt_pending = captured["held_position_monitor_debt_pending"]
    assert callable(monitor_debt_pending)
    assert monitor_debt_pending() is True


def test_main_monitor_bootstrap_blocks_buy_but_keeps_reactor_live(monkeypatch):
    import src.events.reactor as reactor_module
    import src.main as main

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        main,
        "_held_position_monitor_bootstrap_complete",
        threading.Event(),
    )
    monkeypatch.setattr(main, "_start_edli_reactor_wake_listener", lambda: None)
    monkeypatch.setattr(
        main,
        "_edli_live_entry_readiness_block",
        lambda _cfg: (None, {}),
    )
    monkeypatch.setattr(
        main,
        "_held_position_monitor_entry_block_reason",
        lambda: None,
    )
    monkeypatch.setattr(
        main,
        "_held_position_monitor_debt_pending",
        lambda: pytest.fail(
            "a bootstrap entry block must not cancel reduce-only comparison"
        ),
    )
    monkeypatch.setattr(
        reactor_module,
        "run_edli_event_reactor_cycle",
        lambda **kwargs: captured.update(kwargs) or True,
    )

    assert main._edli_event_reactor_cycle() is True
    assert captured["live_entry_block_reason"] == (
        "held_position_monitor_bootstrap_incomplete"
    )
    monitor_pending = captured["held_position_monitor_pending"]
    assert callable(monitor_pending)
    assert monitor_pending() is False


@pytest.mark.parametrize(
    ("evidence", "expected"),
    (
        ({"future_monitor_event_count": 1}, "held_position_monitor_future_evidence"),
        (
            {"future_monitor_event_count": 0, "stale_or_missing_position_count": 2},
            "held_position_monitor_cadence_overdue",
        ),
        (
            {"future_monitor_event_count": 0, "stale_or_missing_position_count": 0},
            None,
        ),
    ),
)
def test_held_position_monitor_entry_block_tracks_current_canonical_debt(
    monkeypatch,
    evidence,
    expected,
):
    import src.main as main
    import src.ops.monitor_cadence as cadence
    import src.state.db as db

    class _ReadConn:
        closed = False

        def close(self):
            self.closed = True

    conn = _ReadConn()
    monkeypatch.setattr(db, "get_trade_connection_read_only", lambda: conn)
    monkeypatch.setattr(
        cadence,
        "collect_monitor_cadence_evidence",
        lambda actual, **_kwargs: evidence if actual is conn else {},
    )

    assert main._held_position_monitor_entry_block_reason() == expected
    assert conn.closed is True


def test_wake_listener_drains_control_before_recurring_work():
    import src.main as main

    source = inspect.getsource(main._run_edli_reactor_wake_listener)
    assert source.index("_consume_live_control_commands()") < source.index(
        "_service_pending_collateral_authority_wake()"
    )
    assert source.index("_consume_live_control_commands()") < source.index(
        "_edli_reactor_wake_poll_once()"
    )


@pytest.mark.parametrize(
    ("job_name", "first_work"),
    (
        ("_edli_command_recovery_cycle", "get_mode()"),
        (
            "_edli_continuous_redecision_screen_cycle",
            '_defer_for_held_position_monitor("edli_continuous_redecision_screen")',
        ),
        (
            "_edli_day0_hourly_refresh_cycle",
            '_defer_for_held_position_monitor("edli_day0_hourly_refresh")',
        ),
    ),
)
def test_active_edli_jobs_drain_control_before_work(job_name, first_work):
    import src.main as main

    source = inspect.getsource(getattr(main, job_name))
    assert source.index("_consume_live_control_commands()") < source.index(first_work)


def test_pause_clear_after_selection_keeps_selected_cycle_no_submit(monkeypatch):
    """A later pause clear cannot reopen the selected snapshot's BUY lane."""

    import src.events.reactor as reactor_module
    import src.main as main

    pause_cleared = [False]
    captured: dict[str, object] = {}

    monkeypatch.setattr(main, "_start_edli_reactor_wake_listener", lambda: None)

    def readiness(_cfg):
        pause_cleared[0] = True
        return (None, {})

    monkeypatch.setattr(main, "_edli_live_entry_readiness_block", readiness)

    def run_cycle(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(reactor_module, "run_edli_event_reactor_cycle", run_cycle)

    assert main._edli_event_reactor_cycle(
        producer_wake_reason="forecast_posterior_advanced",
        allow_paused_forecast_snapshot_completion=True,
    ) is True
    assert pause_cleared[0] is True
    assert captured["live_entry_block_reason"] == (
        "paused_forecast_snapshot_completion"
    )


@pytest.mark.parametrize(
    "failure_mode",
    ("empty", "interrupted", "build_locked", "emit_locked"),
)
@pytest.mark.parametrize("carrier_branch", ("forecast", "day0"))
@pytest.mark.parametrize(
    "initial_risk_level_name",
    ("GREEN", "DATA_DEGRADED", "YELLOW"),
)
def test_published_paused_forecast_wake_materialization_outcome_controls_ack(
    monkeypatch,
    tmp_path,
    failure_mode,
    carrier_branch,
    initial_risk_level_name,
):
    import src.engine.event_reactor_adapter as adapter_module
    import src.events.event_writer as event_writer_module
    import src.events.reactor as reactor_module
    import src.main as main
    import src.observability.status_summary as status_summary
    import src.runtime.bankroll_provider as bankroll_provider
    import src.state.db as db
    import src.state.portfolio as portfolio_module
    from src.events.event_writer import EventWriter
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime import reactor_wake

    family = ("Chicago", "2026-05-25", "high")
    first_decision_time = (
        datetime(2026, 5, 25, 6, 3, tzinfo=timezone.utc)
        if carrier_branch == "day0"
        else datetime(2026, 5, 24, 18, 3, tzinfo=timezone.utc)
    )
    carrier = _forecast_event(
        f"latest-carrier-{carrier_branch}-{failure_mode}",
        target_date=family[1],
    )
    if carrier_branch == "day0":
        carrier = replace(
            carrier,
            observed_at="2026-05-25T06:00:00+00:00",
            available_at="2026-05-25T06:01:00+00:00",
            received_at="2026-05-25T06:02:00+00:00",
        )

    world_path = tmp_path / f"world-{carrier_branch}-{failure_mode}.db"
    forecasts_path = tmp_path / f"forecasts-{carrier_branch}-{failure_mode}.db"
    trade_path = tmp_path / f"trades-{carrier_branch}-{failure_mode}.db"
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    world = sqlite3.connect(world_path)
    init_schema(world)
    ordinary = _forecast_event(f"paused-ordinary-{failure_mode}")
    EventStore(world).insert_or_ignore(ordinary)
    materialized_carrier = carrier
    if carrier_branch == "day0":
        EventStore(world).insert_or_ignore(
            _day0_event_for_target(
                f"paused-carrier-prior-{failure_mode}",
                family[1],
                "2026-05-25T05:59:00+00:00",
            )
        )
        day0_carriers = _build_day0_posterior_redecision_events(
            world,
            (carrier,),
            day0_families={family},
            received_at=first_decision_time.isoformat(),
        )
        assert len(day0_carriers) == 1
        materialized_carrier = day0_carriers[0]
        assert materialized_carrier.event_type == "DAY0_EXTREME_UPDATED"
    world.commit()
    world.close()
    sqlite3.connect(forecasts_path).close()
    sqlite3.connect(trade_path).close()

    wake = reactor_wake.publish_reactor_wake(
        source="replacement_forecast_materializer",
        reason="forecast_posterior_advanced",
        path=wake_path,
        wake_id=f"posterior-wake-{failure_mode}",
        published_at=first_decision_time,
        forecast_families=(family,),
    )
    wake_queue_file = reactor_wake._wake_queue_target(wake, path=wake_path)
    wake_queue_bytes = wake_queue_file.read_bytes()
    day0_wake = reactor_wake.publish_reactor_wake(
        source="day0",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id=f"pure-entry-day0-{failure_mode}",
        published_at=first_decision_time + timedelta(seconds=1),
        forecast_families=(family,),
    )
    day0_queue_file = reactor_wake._wake_queue_target(day0_wake, path=wake_path)
    day0_queue_bytes = day0_queue_file.read_bytes()
    venue_buy_commands: list[str] = []
    batch_identities: list[tuple[str, str]] = []
    write_outcomes: list[tuple[tuple[bool, bool], ...]] = []
    calls = {"auction": 0, "claim": 0, "requeue": 0, "adapter": 0, "trade_conn": 0}
    failure = [failure_mode]
    paused = [True]
    risk_level = [getattr(RiskLevel, initial_risk_level_name)]

    class TestClock(datetime):
        current = first_decision_time

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.current.replace(tzinfo=None)
            return cls.current.astimezone(tz)

    def build_carrier(*_args, **kwargs):
        day0_call = bool(kwargs.get("phase_filter_exempt_families"))
        if day0_call != (carrier_branch == "day0"):
            return []
        if failure[0] == "empty":
            return []
        if failure[0] == "interrupted":
            raise sqlite3.OperationalError("interrupted")
        if failure[0] == "build_locked":
            raise sqlite3.OperationalError("database is locked")
        return [carrier]

    original_write_many = EventWriter.write_many

    def write_many(self, events):
        if failure[0] == "emit_locked":
            raise sqlite3.OperationalError("database is locked")
        results = original_write_many(self, events)
        write_outcomes.append(
            tuple((result.inserted, result.duplicate) for result in results)
        )
        return results

    class SubmitAdapter:
        _live_submit_count = [0]
        _live_ack_count = [0]

        def __call__(self, *_args, **_kwargs):
            venue_buy_commands.append("direct_submit")
            raise AssertionError("forecast carrier must use the global batch seam")

        def process_global_batch(
            self,
            events,
            _decision_time,
            *,
            claim_unpaged_winner=None,
        ):
            calls["auction"] += 1
            assert claim_unpaged_winner is not None
            batch_identities.extend(
                (event.event_id, event.causal_snapshot_id) for event in events
            )
            reason = (
                "entries_paused:operator"
                if paused[0]
                else "GLOBAL_AUCTION_NO_TRADE:NO_CURRENT_EXECUTABLE_POSITIVE_ORDER"
            )
            return GlobalBatchSubmitResult(
                receipts={
                    event.event_id: EventSubmissionReceipt(
                        submitted=False,
                        event_id=event.event_id,
                        causal_snapshot_id=event.causal_snapshot_id,
                        reason=reason,
                        proof_accepted=False,
                    )
                    for event in events
                },
                winner_event_id=None,
                venue_submit_count=0,
                economic_cut_completed=not paused[0],
            )

    submit_adapter = SubmitAdapter()

    def venue_adapter(*_args, **_kwargs):
        calls["adapter"] += 1
        return submit_adapter

    def trade_connection(**_kwargs):
        calls["trade_conn"] += 1
        return sqlite3.connect(trade_path)

    original_claim = EventStore.claim
    original_requeue = EventStore.requeue_pending

    def claim(self, *args, **kwargs):
        calls["claim"] += 1
        return original_claim(self, *args, **kwargs)

    def requeue(self, *args, **kwargs):
        calls["requeue"] += 1
        return original_requeue(self, *args, **kwargs)

    def world_connection():
        conn = sqlite3.connect(world_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda *_args, **_kwargs: {"enabled": True, "event_writer_enabled": True},
    )
    monkeypatch.setattr(main, "_start_edli_reactor_wake_listener", lambda: None)
    monkeypatch.setattr(
        main,
        "_edli_live_entry_readiness_block",
        lambda _cfg: (None, {}),
    )
    monkeypatch.setattr(
        main,
        "_held_position_monitor_entry_block_reason",
        lambda: None,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_paused_forecast_carrier_priority_allowed",
        lambda **_kwargs: paused[0],
    )
    monkeypatch.setattr(main, "_forecast_wake_held_families", lambda _families: frozenset())
    monkeypatch.setattr(main, "_edli_next_redecision_source", lambda: "pause-antibody")
    monkeypatch.setattr(main, "_edli_build_forecast_snapshot_events", build_carrier)
    monkeypatch.setattr(
        main,
        "_edli_refresh_global_allocator",
        lambda *_args, **_kwargs: {
            "configured": False,
            "entry": {"reason": "listener_test_no_capital_authority"},
        },
    )
    monkeypatch.setattr(
        main,
        "_edli_acquire_mutex",
        lambda mutex, *, timeout: mutex.acquire(timeout=timeout),
    )
    monkeypatch.setattr(
        main,
        "_start_venue_background_maintenance_after_reactor_if_required",
        lambda: None,
    )
    monkeypatch.setattr(main, "_edli_reactor_active_lock", threading.Lock())
    monkeypatch.setattr(riskguard, "get_current_level", lambda: risk_level[0])
    monkeypatch.setattr(
        adapter_module,
        "_entry_pause_blocks_live_submit",
        lambda _conn: "operator_pause" if paused[0] else None,
    )
    monkeypatch.setattr(
        adapter_module,
        "event_bound_live_adapter_from_trade_conn",
        venue_adapter,
    )
    monkeypatch.setattr(adapter_module, "edli_source_truth_gate", lambda _event: True)
    monkeypatch.setattr(
        adapter_module,
        "executable_snapshot_gate_from_trade_conn",
        lambda *_args, **_kwargs: (lambda *_gate_args, **_gate_kwargs: True),
    )
    monkeypatch.setattr(
        adapter_module,
        "riskguard_allows_new_entries",
        lambda **_kwargs: (lambda _event: True),
    )
    monkeypatch.setattr(event_writer_module.EventWriter, "write_many", write_many)
    monkeypatch.setattr(bankroll_provider, "warm_from_collateral_snapshot", lambda: True)
    monkeypatch.setattr(portfolio_module, "load_runtime_open_portfolio", lambda _conn: object())
    monkeypatch.setattr(status_summary, "write_cycle_result", lambda _pulse: None)
    monkeypatch.setattr(db, "ZEUS_FORECASTS_DB_PATH", forecasts_path)
    monkeypatch.setattr(db, "get_world_connection", world_connection)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda: sqlite3.connect(forecasts_path),
    )
    monkeypatch.setattr(
        db,
        "get_trade_connection_with_world_required",
        trade_connection,
    )
    monkeypatch.setattr(db, "world_write_mutex", lambda: threading.Lock())
    monkeypatch.setattr("src.config.state_path", lambda filename: tmp_path / filename)
    monkeypatch.setattr(reactor_module, "datetime", TestClock)
    monkeypatch.setattr(
        reactor_module,
        "_edli_reactor_held_family_provider",
        lambda: (lambda: frozenset()),
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_held_sell_request_exposure_provider",
        lambda: (lambda: frozenset()),
    )
    monkeypatch.setattr(
        reactor_module,
        "_current_local_day_families",
        lambda *_args, **_kwargs: ({family} if carrier_branch == "day0" else set()),
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_decision_family_snapshot_refresher",
        lambda _conn: (lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_reactor_cycle_advance_enqueuer",
        lambda: (lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_reactor_day0_hourly_refresher",
        lambda: (lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_reactor_family_market_absence_provider",
        lambda: (lambda *_args, **_kwargs: False),
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_pre_submit_authority_provider_from_book_evidence_conn",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(EventStore, "claim", claim)
    monkeypatch.setattr(EventStore, "requeue_pending", requeue)
    monkeypatch.setattr(reactor_wake, "reactor_urgent_wake_revision", lambda: "stable")
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda *_args, **_kwargs: (),
    )
    main._edli_initialize_reactor_wake_cursor()
    main._yield_incomplete_day0_after_monitor_once(
        day0_wake,
        monitor_succeeded=False,
    )
    assert reactor_wake.read_reactor_wake(path=wake_path) == day0_wake
    main._yield_incomplete_day0_after_monitor_once(
        day0_wake,
        monitor_succeeded=True,
    )

    first_poll = main._edli_reactor_wake_poll_once()

    check = sqlite3.connect(world_path)
    assert check.execute(
        "SELECT processing_status, attempt_count FROM opportunity_event_processing WHERE event_id = ?",
        (ordinary.event_id,),
    ).fetchone() == ("pending", 0)
    carrier_after_wake = check.execute(
        "SELECT processing_status, attempt_count, claimed_at "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (materialized_carrier.event_id,),
    ).fetchone()
    check.close()
    assert venue_buy_commands == []
    if failure_mode == "empty":
        assert first_poll is False
        assert main._edli_last_reactor_wake_id is None
        assert carrier_after_wake is None
        assert wake_queue_file.read_bytes() == wake_queue_bytes
        assert day0_queue_file.read_bytes() == day0_queue_bytes
        assert reactor_wake.read_reactor_wake(path=wake_path) == day0_wake
        assert batch_identities == []
        assert write_outcomes == []
        assert calls == {
            "auction": 0,
            "claim": 0,
            "requeue": 0,
            "adapter": 0,
            "trade_conn": 0,
        }
        return

    assert first_poll is False
    assert main._edli_last_reactor_wake_id is None
    assert carrier_after_wake is None
    assert wake_queue_file.read_bytes() == wake_queue_bytes
    assert day0_queue_file.read_bytes() == day0_queue_bytes
    assert calls == {
        "auction": 0,
        "claim": 0,
        "requeue": 0,
        "adapter": 0,
        "trade_conn": 0,
    }
    assert (
        reactor_wake.read_reactor_wake(
            path=wake_path,
        )
        == day0_wake
    )

    failure[0] = None
    main._yield_incomplete_day0_after_monitor_once(
        day0_wake,
        monitor_succeeded=True,
    )
    assert main._edli_reactor_wake_poll_once() is True
    check = sqlite3.connect(world_path)
    assert check.execute(
        "SELECT processing_status, attempt_count, claimed_at "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (materialized_carrier.event_id,),
    ).fetchone() == ("pending", 0, None)
    check.close()
    assert not wake_queue_file.exists()
    assert calls == {
        "auction": 0,
        "claim": 0,
        "requeue": 0,
        "adapter": 0,
        "trade_conn": 0,
    }
    assert write_outcomes[-1] == ((True, False),)
    assert reactor_wake.acknowledge_reactor_wake(day0_wake, path=wake_path)

    # Replaying the same wake reuses the durable carrier identity and still
    # bypasses auction, claim, and requeue.
    assert reactor_module.run_edli_event_reactor_cycle(
        active_lock=main._edli_reactor_active_lock,
        producer_wake_reason=wake.reason,
        producer_wake_ids=(wake.wake_id,),
        producer_wake_published_at=wake.published_at,
        producer_wake_families=wake.forecast_families,
        allow_paused_forecast_snapshot_completion=True,
    ) is True
    check = sqlite3.connect(world_path)
    assert check.execute(
        "SELECT COUNT(*) FROM opportunity_event_processing WHERE event_id = ?",
        (materialized_carrier.event_id,),
    ).fetchone() == (1,)
    assert check.execute(
        "SELECT processing_status, attempt_count, claimed_at "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (materialized_carrier.event_id,),
    ).fetchone() == ("pending", 0, None)
    check.close()
    assert calls == {
        "auction": 0,
        "claim": 0,
        "requeue": 0,
        "adapter": 0,
        "trade_conn": 0,
    }
    assert write_outcomes[-1] == ((False, True),)

    risk_level[0] = RiskLevel.GREEN
    paused[0] = False
    TestClock.current = first_decision_time + timedelta(seconds=2)
    resumed_wake = reactor_wake.publish_reactor_wake(
        source="replacement_forecast_materializer",
        reason="forecast_posterior_advanced",
        path=wake_path,
        wake_id=f"posterior-wake-{failure_mode}-pause-clear",
        published_at=TestClock.current,
        forecast_families=(family,),
    )
    resumed_queue_file = reactor_wake._wake_queue_target(
        resumed_wake,
        path=wake_path,
    )
    recovered_poll = main._edli_reactor_wake_poll_once()
    check = sqlite3.connect(world_path)
    carrier_after_pause = check.execute(
        "SELECT processing_status, attempt_count, claimed_at "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (materialized_carrier.event_id,),
    ).fetchone()
    assert recovered_poll is True, (
        carrier_after_pause,
        tuple(batch_identities),
        main._edli_last_reactor_wake_id,
        reactor_wake.read_reactor_wake(path=wake_path),
    )
    assert tuple(carrier_after_pause[:2]) == ("processed", 1)
    assert check.execute(
        "SELECT processing_status, attempt_count FROM opportunity_event_processing WHERE event_id = ?",
        (ordinary.event_id,),
    ).fetchone() == ("pending", 0)
    check.close()
    assert not resumed_queue_file.exists()

    assert calls["auction"] == 1
    assert calls["claim"] > 0
    assert calls["trade_conn"] > 0
    assert calls["adapter"] > 0
    assert venue_buy_commands == []


def test_paused_exact_held_sell_parks_when_exposure_provider_is_unavailable(caplog):
    from src.events.reactor import _paused_entry_wake_should_park
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    request = make_held_sell_reauction_request(
        position_id="held-position-unavailable",
        family=("Dallas", "2026-05-24", "high"),
        probability_content_identity="q-held-unavailable",
        held_token_id="held-token-unavailable",
        held_best_bid=0.12,
        bid_observed_at="2026-05-24T17:59:00+00:00",
    )

    def _raise():
        raise RuntimeError("trade DB unavailable")

    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        held_sell_reauction_requests=(request,),
        held_sell_request_exposure_provider=_raise,
    ) is True
    assert _paused_entry_wake_should_park(
        pause_reason="operator",
        held_sell_reauction_requests=(request,),
        held_sell_request_exposure_provider=lambda: None,
    ) is True
    assert "exact exposure unavailable; parking debt" in caplog.text


def test_paused_debt_drains_once_after_canonical_family_materializes(tmp_path):
    from src.events import reactor
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    request = reactor_wake.make_held_sell_reauction_request(
        position_id="paused-debt-no-held",
        family=("Dallas", "2026-07-25", "high"),
        probability_content_identity="q-paused-debt",
        held_token_id="token-paused-debt",
        held_best_bid=0.12,
        bid_observed_at="2026-07-25T12:00:00+00:00",
    )
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="paused-debt-wake",
        forecast_families=(request.family,),
        held_sell_reauction_requests=(request,),
    )

    assert reactor._paused_entry_wake_should_park(
        pause_reason="operator",
        held_sell_reauction_requests=(request,),
        held_sell_request_exposure_provider=lambda: frozenset(),
    ) is True
    held_positions = set()
    assert reactor._paused_entry_wake_should_park(
        pause_reason="operator",
        held_sell_reauction_requests=(request,),
        held_sell_request_exposure_provider=lambda: frozenset(held_positions),
    ) is True
    held_positions.add((request.position_id, request.family))
    assert reactor._paused_entry_wake_should_park(
        pause_reason="operator",
        held_sell_reauction_requests=(request,),
        held_sell_request_exposure_provider=lambda: frozenset(held_positions),
    ) is False

    cut_result = _held_sell_completion_result(
        position_id=request.position_id,
        token_id=request.held_token_id,
        probability_content_identity=request.probability_content_identity,
    )
    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=cut_result,
    )
    assert len(receipts) == 1
    assert reactor_wake.persist_held_sell_reauction_receipts(
        receipts,
        path=path,
    ) is True
    assert reactor_wake.held_sell_reauction_requests_completed(
        (request,),
        path=path,
    ) is True
    assert reactor_wake.read_reactor_wake(path=path) == wake
    assert reactor_wake.acknowledge_reactor_wake(wake, path=path) is True
    assert reactor_wake.read_reactor_wake(path=path) is None


def test_paused_no_held_cycle_parks_before_active_lock(monkeypatch):
    import src.engine.event_reactor_adapter as adapter_module
    import src.events.reactor as reactor_module
    import src.main as main
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda *_args, **_kwargs: {"enabled": True, "event_writer_enabled": True},
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(
        reactor_module,
        "_edli_reactor_held_family_provider",
        lambda: (lambda: frozenset()),
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_held_sell_request_exposure_provider",
        lambda: (lambda: frozenset()),
    )
    monkeypatch.setattr(
        adapter_module,
        "_entry_pause_blocks_live_submit",
        lambda _conn: "operator_pause",
    )
    drains = []
    monkeypatch.setattr(
        reactor_module,
        "_edli_prune_paused_mutable_working_set",
        lambda: drains.append(True) or {"forecast_snapshot": 0, "day0": 0},
    )

    lock = threading.Lock()
    assert reactor_module.run_edli_event_reactor_cycle(active_lock=lock) is False
    assert drains == [True]
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_paused_mutable_drain_is_bounded_and_cadence_limited(monkeypatch):
    import src.events.reactor as reactor_module
    import src.main as main

    calls = []

    class _WriteLock:
        def acquire(self, *, timeout):
            calls.append(("lock", timeout))
            return True

        def release(self):
            calls.append("unlock")

    class _Conn:
        def execute(self, sql):
            calls.append(("execute", sql))

        def set_progress_handler(self, callback, steps):
            calls.append(("progress", callback is not None, steps))

        def commit(self):
            calls.append("commit")

        def rollback(self):
            calls.append("rollback")

        def close(self):
            calls.append("close")

    class _Store:
        def __init__(self, conn):
            calls.append(("store", conn))

        def archive_superseded_forecast_snapshot_events(self, *, batch_limit):
            calls.append(("fsr", batch_limit))
            return 12

        def archive_superseded_day0_events(self, *, batch_limit):
            calls.append(("day0", batch_limit))
            return 3

    conn = _Conn()
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda *_args, **_kwargs: {
            "reactor_prune_interval_seconds": 60,
            "reactor_prune_batch_limit": 5000,
            "reactor_prune_budget_seconds": 6,
            "reactor_prune_lock_timeout_seconds": 0.5,
        },
    )
    monkeypatch.setattr(reactor_module, "world_write_mutex", lambda: _WriteLock())
    monkeypatch.setattr(
        "src.state.db.get_world_connection",
        lambda **_kwargs: conn,
    )
    monkeypatch.setattr(reactor_module, "EventStore", _Store)
    monkeypatch.setattr(reactor_module, "_EDLI_LAST_PAUSED_PRUNE_MONOTONIC", None)
    monkeypatch.setattr(
        reactor_module,
        "_EDLI_LAST_PAUSED_PRUNE_ATTEMPT_MONOTONIC",
        None,
    )

    assert reactor_module._edli_prune_paused_mutable_working_set() == {
        "forecast_snapshot": 12,
        "day0": 3,
    }
    first_calls = list(calls)
    assert reactor_module._edli_prune_paused_mutable_working_set() == {
        "forecast_snapshot": 0,
        "day0": 0,
    }

    assert calls == first_calls
    assert ("fsr", 5000) in calls
    assert ("day0", 5000) in calls
    assert "commit" in calls
    assert "rollback" not in calls


def test_paused_drain_does_not_race_an_active_reactor(monkeypatch):
    import src.engine.event_reactor_adapter as adapter_module
    import src.events.reactor as reactor_module
    import src.main as main
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    drains = []

    class _RacedLock:
        def locked(self):
            return False

        def acquire(self, *, blocking=False):
            return False

        def release(self):
            raise AssertionError("unowned reactor lock must not be released")

    monkeypatch.setattr(main, "_settings_section", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(
        reactor_module,
        "_edli_held_sell_request_exposure_provider",
        lambda: (lambda: frozenset()),
    )
    monkeypatch.setattr(
        adapter_module,
        "_entry_pause_blocks_live_submit",
        lambda _conn: "operator_pause",
    )
    monkeypatch.setattr(
        reactor_module,
        "_edli_prune_paused_mutable_working_set",
        lambda: drains.append(True),
    )

    assert reactor_module.run_edli_event_reactor_cycle(active_lock=_RacedLock()) is False
    assert drains == []


def test_paused_exact_canonical_held_sell_request_reaches_reduce_only_cycle(monkeypatch):
    import src.engine.event_reactor_adapter as adapter_module
    import src.events.reactor as reactor_module
    import src.main as main
    import src.state.db as db
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    class AuctionReached(RuntimeError):
        pass

    request = make_held_sell_reauction_request(
        position_id="paused-exact-held",
        family=("Dallas", "2026-05-24", "high"),
        probability_content_identity="q-paused-exact-held",
        held_token_id="token-paused-exact-held",
        held_best_bid=0.12,
        bid_observed_at="2026-05-24T17:59:00+00:00",
    )
    monkeypatch.setattr(main, "_settings_section", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(
        reactor_module,
        "_edli_held_sell_request_exposure_provider",
        lambda: (lambda: frozenset({(request.position_id, request.family)})),
    )
    monkeypatch.setattr(
        adapter_module,
        "_entry_pause_blocks_live_submit",
        lambda _conn: "operator_pause",
    )
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda: (_ for _ in ()).throw(AuctionReached()),
    )

    lock = threading.Lock()
    with pytest.raises(AuctionReached):
        reactor_module.run_edli_event_reactor_cycle(
            active_lock=lock,
            producer_wake_reason="held_sell_global_auction_completion_requested",
            producer_held_sell_reauction_requests=(request,),
            held_position_monitor_debt_pending=lambda: True,
        )
    assert lock.locked() is False


def test_exact_held_sell_completion_builds_current_day_redecision_carrier(
    monkeypatch,
):
    import src.events.reactor as reactor_module

    family = ("Dallas", "2026-05-24", "high")
    decision_time = datetime(2026, 5, 24, 18, 0, tzinfo=timezone.utc)
    calls = []

    monkeypatch.setattr(
        reactor_module,
        "_current_local_day_families",
        lambda families, *, decision_time: calls.append(
            (set(families), decision_time)
        )
        or {family},
    )

    result = reactor_module._day0_redecision_carrier_families(
        producer_wake_reason=reactor_module.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        forecast_wake_families={family},
        decision_time=decision_time,
    )

    assert result == {family}
    assert calls == [({family}, decision_time)]
    assert reactor_module._day0_redecision_carrier_families(
        producer_wake_reason="market_price_advanced",
        forecast_wake_families={family},
        decision_time=decision_time,
    ) == set()
    assert calls == [({family}, decision_time)]


def test_degraded_durable_held_sell_debt_reaches_reduce_only_setup(monkeypatch):
    import src.engine.event_reactor_adapter as adapter_module
    import src.events.reactor as reactor_module
    import src.main as main
    import src.state.db as db
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    class ReduceOnlySetupReached(RuntimeError):
        pass

    monkeypatch.setattr(main, "_settings_section", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.YELLOW)
    monkeypatch.setattr(
        reactor_module,
        "_durable_exact_held_sell_completion_pending",
        lambda: True,
    )
    monkeypatch.setattr(
        adapter_module,
        "_entry_pause_blocks_live_submit",
        lambda _conn: "restart_guard",
    )
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda: (_ for _ in ()).throw(ReduceOnlySetupReached()),
    )

    lock = threading.Lock()
    with pytest.raises(ReduceOnlySetupReached):
        reactor_module.run_edli_event_reactor_cycle(active_lock=lock)
    assert lock.locked() is False


def test_no_submit_claim_debt_cannot_requeue_newer_aba_generation():
    conn, store = _store()
    event = _forecast_event("claim-drain-aba")
    store.insert_or_ignore(event)
    old_claimed_at = "2026-05-24T18:30:00+00:00"
    new_claimed_at = old_claimed_at
    assert store.claim(event.event_id, claimed_at=old_claimed_at)
    store.requeue_pending(event.event_id)
    assert store.claim(event.event_id, claimed_at=new_claimed_at)
    conn.commit()

    reactor = OpportunityEventReactor.__new__(OpportunityEventReactor)
    reactor._store = store
    reactor._cycle_entry_gate = lambda: False
    reactor._no_submit_claim_requeue_debt = {
        event.event_id: (old_claimed_at, 1)
    }

    reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    row = conn.execute(
        """
        SELECT processing_status, claimed_at
          FROM opportunity_event_processing
         WHERE consumer_name = ? AND event_id = ?
        """,
        (store.consumer_name, event.event_id),
    ).fetchone()
    assert tuple(row) == ("processing", new_claimed_at)
    assert reactor._no_submit_claim_requeue_debt == {}


def test_finalize_mutex_bounce_records_only_proven_no_submit_claim_debt(
    monkeypatch,
):
    from src.events import reactor as reactor_module

    stall_lock = threading.Lock()
    monkeypatch.setattr(
        reactor_module,
        "world_write_mutex",
        lambda: stall_lock,
    )
    reactor = OpportunityEventReactor.__new__(OpportunityEventReactor)
    reactor._submit = SimpleNamespace()
    reactor._no_submit_claim_requeue_debt = {}
    claimed_at = "2026-05-24T18:30:00+00:00"

    assert stall_lock.acquire(timeout=1.0)
    try:
        no_submit = _forecast_event("claim-debt-no-submit")
        result = ReactorResult()
        finalized = reactor._finalize_deferred_event_unit(
            no_submit,
            EventSubmissionReceipt(False, no_submit.event_id),
            decision_time=_DT_VENUE_OPEN,
            result=result,
            wait_ms=0,
            claim_generation=claimed_at,
            claim_attempt_count=3,
        )
        assert finalized is False
        assert reactor._no_submit_claim_requeue_debt == {
            no_submit.event_id: (claimed_at, 3)
        }

        side_effect_unknown = _forecast_event("claim-debt-side-effect")
        reactor._finalize_deferred_event_unit(
            side_effect_unknown,
            EventSubmissionReceipt(
                False,
                side_effect_unknown.event_id,
                venue_call_started=True,
            ),
            decision_time=_DT_VENUE_OPEN,
            result=ReactorResult(),
            wait_ms=0,
            claim_generation=claimed_at,
            claim_attempt_count=1,
        )
        assert side_effect_unknown.event_id not in (
            reactor._no_submit_claim_requeue_debt
        )
    finally:
        stall_lock.release()


def test_no_submit_double_db_lock_failure_drains_on_next_wake(
    monkeypatch,
    tmp_path,
):
    from src.events import reactor as reactor_module

    db_path = tmp_path / "no-submit-claim-drain.db"
    conn = sqlite3.connect(db_path, timeout=0)
    init_schema(conn)
    store = EventStore(conn)
    event = _forecast_event("claim-debt-double-lock")
    store.insert_or_ignore(event)
    claimed_at = "2026-05-24T18:30:00+00:00"
    assert store.claim(event.event_id, claimed_at=claimed_at)
    conn.commit()

    local_mutex = threading.Lock()
    monkeypatch.setattr(
        reactor_module,
        "world_write_mutex",
        lambda: local_mutex,
    )
    reactor = OpportunityEventReactor.__new__(OpportunityEventReactor)
    reactor._store = store
    reactor._submit = SimpleNamespace()
    reactor._no_submit_claim_requeue_debt = {}

    blocker = sqlite3.connect(db_path, timeout=0)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        finalized = reactor._finalize_deferred_event_unit(
            event,
            EventSubmissionReceipt(False, event.event_id),
            decision_time=_DT_VENUE_OPEN,
            result=ReactorResult(),
            wait_ms=0,
            claim_generation=claimed_at,
            claim_attempt_count=1,
        )
        assert finalized is False
        assert reactor._no_submit_claim_requeue_debt == {
            event.event_id: (claimed_at, 1)
        }
    finally:
        blocker.rollback()
        blocker.close()

    reactor._cycle_entry_gate = lambda: False
    reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    row = conn.execute(
        """
        SELECT processing_status, claimed_at, last_error
          FROM opportunity_event_processing
         WHERE consumer_name = ? AND event_id = ?
        """,
        (store.consumer_name, event.event_id),
    ).fetchone()
    assert tuple(row) == (
        "pending",
        None,
        _POST_SUBMIT_WORLD_WRITE_LOCK_RETRY,
    )
    assert reactor._no_submit_claim_requeue_debt == {}


def test_global_batch_claim_tokens_drain_paged_and_unpaged_no_submit_losers(
    tmp_path,
):
    db_path = tmp_path / "global-no-submit-claim-drain.db"
    conn = sqlite3.connect(db_path, timeout=0)
    init_schema(conn)
    store = EventStore(conn)
    paged = _forecast_event(
        "global-claim-drain-paged",
        target_date="2026-05-25",
    )
    unpaged = _forecast_event(
        "global-claim-drain-unpaged",
        target_date="2026-05-26",
    )
    store.insert_or_ignore(paged)
    conn.commit()

    reactor = _global_batch_probe_reactor(store, {})
    blocker = sqlite3.connect(db_path, timeout=0)
    blocker_held = False

    def _batch(events, _decision_time, *, claim_unpaged_winner=None):
        nonlocal blocker_held
        assert claim_unpaged_winner is not None
        claimed_unpaged = claim_unpaged_winner(unpaged)
        assert claimed_unpaged is not None
        blocker.execute("BEGIN IMMEDIATE")
        blocker_held = True
        receipts = {
            event.event_id: EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="SUBMIT_ABORTED_PRICE_MOVED:TEST_FINALIZE_LOCK",
            )
            for event in (*events, claimed_unpaged)
        }
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=None,
            venue_submit_count=0,
        )

    reactor._submit.process_global_batch = _batch
    try:
        reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)
        assert blocker_held
        processing_rows = conn.execute(
            """
            SELECT event_id, claimed_at, attempt_count
              FROM opportunity_event_processing
             WHERE consumer_name = ?
               AND event_id IN (?, ?)
             ORDER BY event_id
            """,
            (store.consumer_name, paged.event_id, unpaged.event_id),
        ).fetchall()
        assert len(processing_rows) == 2
        assert all(row[1] and row[2] == 1 for row in processing_rows)
        assert reactor._no_submit_claim_requeue_debt == {
            str(row[0]): (str(row[1]), int(row[2]))
            for row in processing_rows
        }
    finally:
        if blocker_held:
            blocker.rollback()
        blocker.close()

    reactor._cycle_entry_gate = lambda: False
    reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    drained_rows = conn.execute(
        """
        SELECT processing_status, claimed_at, last_error
          FROM opportunity_event_processing
         WHERE consumer_name = ?
           AND event_id IN (?, ?)
         ORDER BY event_id
        """,
        (store.consumer_name, paged.event_id, unpaged.event_id),
    ).fetchall()
    assert [tuple(row) for row in drained_rows] == [
        ("pending", None, _POST_SUBMIT_WORLD_WRITE_LOCK_RETRY),
        ("pending", None, GLOBAL_WINNER_TARGETED_CLAIM),
    ]
    assert reactor._no_submit_claim_requeue_debt == {}


def _processing_status(conn: sqlite3.Connection, event_id: str) -> str | None:
    row = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    return None if row is None else str(row[0])


def test_global_family_ineligible_is_explicitly_transient(caplog):
    reason = (
        "GLOBAL_FAMILY_INELIGIBLE:GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:"
        "REPLACEMENT_RAW_INPUT_HWM"
    )

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert "GLOBAL_FAMILY_INELIGIBLE" in TRANSIENT_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is True

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


def test_global_reauction_epoch_expiry_is_explicitly_transient(caplog):
    reason = "GLOBAL_REAUCTION_EPOCH_EXPIRED"

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert reason in TRANSIENT_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is True

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


def test_duplicate_same_token_pre_submit_rejection_is_terminal(caplog):
    reason = "duplicate_entry_same_token:open_or_filled_entry_command_same_token"

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert "duplicate_entry_same_token" in TERMINAL_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is False

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


def test_selected_family_forecast_authority_loss_is_transient(caplog):
    reason = (
        "LIVE_INFERENCE_INPUTS_MISSING:"
        "FORECAST_AUTHORITY_MISSING:replacement_posterior"
    )

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert "FORECAST_AUTHORITY_MISSING" in TRANSIENT_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is True

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


@pytest.mark.parametrize(
    "reason_base",
    (
        "GLOBAL_DAY0_CONDITIONING_OBSERVATION_MISMATCH",
        "GLOBAL_DAY0_CONDITIONING_OBSERVATION_TIME_MISMATCH",
    ),
)
def test_day0_observation_correction_mismatch_requeues_and_reseeds(
    caplog,
    reason_base,
):
    reason = (
        "GLOBAL_PREPARED_FAMILY_INCOMPLETE:"
        f"{reason_base}"
    )

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert reason_base in TRANSIENT_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is True
        assert _is_posterior_staleness_reason(reason) is True

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


def test_maker_taker_forbidden_certificate_race_is_refreshable():
    from src.events.reactor import _is_executable_snapshot_refresh_reason

    reason = (
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:"
        "EDLI_LIVE_QKERNEL_SELECTED_BOOK_CANDIDATE_REJECTED:"
        "QKERNEL_REST_THEN_CROSS_NOT_ACTIONABLE:policy=MAKER_TAKER_FORBIDDEN"
    )

    assert _is_transient_money_path_reason(reason) is True
    assert _is_executable_snapshot_refresh_reason(reason) is True


def test_day0_catchup_emitter_returns_exact_event_ids(monkeypatch):
    from src.events import reactor as reactor_module

    monkeypatch.setattr(
        reactor_module,
        "_edli_scan_day0_with_lock_retry",
        lambda **_kwargs: (
            [SimpleNamespace(event_id="day0-authority", inserted=True)],
            [
                SimpleNamespace(event_id="day0-observation", inserted=True),
                SimpleNamespace(event_id="day0-authority", inserted=False),
            ],
        ),
    )
    world = sqlite3.connect(":memory:")
    trade = sqlite3.connect(":memory:")
    try:
        event_ids = _edli_emit_day0_extreme_events(
            world,
            trade,
            decision_time=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc).isoformat(),
            limit=3,
        )
    finally:
        trade.close()
        world.close()

    assert event_ids == ("day0-authority", "day0-observation")


@pytest.mark.parametrize(
    ("city", "station_id"),
    (("Kuala Lumpur", "WMKK"), ("Manila", "RPLL")),
)
def test_held_day0_catchup_wakes_once_and_keeps_nonheld_suppression(
    monkeypatch,
    city,
    station_id,
):
    """Held authority persists once; duplicate and non-held paths stay quiet."""
    from src.events import reactor as reactor_module
    from src.events.event_writer import EventWriter
    from src.events.triggers.day0_extreme_updated import (
        Day0ExtremeUpdatedTrigger,
        build_day0_extreme_updated_event,
    )
    from src.runtime import reactor_wake

    class _Semantics:
        def round_single(self, value):
            return int(value)

    decision_time = datetime(2026, 7, 30, 6, 0, tzinfo=timezone.utc)
    family = (city, "2026-07-30", "high")
    admitted_family = reactor_module._substrate_refresh_family_key(*family)
    context_id = f"{station_id.lower()}-jul30"
    base_observation = {
        "city": family[0],
        "target_date": family[1],
        "metric": family[2],
        "settlement_source": "wu_icao_history",
        "station_id": station_id,
        "observation_time": "2026-07-30T05:00:00+00:00",
        "observation_available_at": "2026-07-30T05:05:00+00:00",
        "raw_value": 31.0,
        "high_so_far": 31.0,
        "low_so_far": 25.0,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "observation_context_id": context_id,
    }
    refreshed_observation = {
        **base_observation,
        "observation_time": "2026-07-30T05:30:00+00:00",
        "observation_available_at": "2026-07-30T05:35:00+00:00",
    }
    world = sqlite3.connect(":memory:")
    trade = sqlite3.connect(":memory:")
    init_schema(world)
    try:
        prior = build_day0_extreme_updated_event(
            observation=base_observation,
            settlement_semantics=_Semantics(),
            decision_time=decision_time,
            received_at="2026-07-30T05:06:00+00:00",
        )
        assert EventWriter(world).write(prior).inserted
        world.execute(
            """
            INSERT INTO no_trade_regret_events (
                regret_event_id, event_id, rejection_stage, rejection_reason,
                regret_bucket, decision_time, city, target_date, metric,
                family_id, causal_snapshot_id, created_at, schema_version
            ) VALUES (?, ?, 'TRADE_SCORE', 'EVENT_BOUND_ALL_CANDIDATES_REJECTED:none',
                      'NO_EDGE', ?, ?, ?, ?, 'family-kl-high', ?, ?, 1)
            """,
            (
                f"regret-{prior.event_id}",
                prior.event_id,
                "2026-07-30T05:40:00+00:00",
                *family,
                context_id,
                "2026-07-30T05:40:00+00:00",
            ),
        )
        assert Day0ExtremeUpdatedTrigger(
            EventWriter(world),
            suppress_recent_no_value_refutations=True,
        ).emit_from_observation(
            observation=refreshed_observation,
            settlement_semantics=_Semantics(),
            decision_time=decision_time,
            received_at="2026-07-30T05:40:00+00:00",
        ) is None

        scan_observation = [refreshed_observation]

        def _scan(*, trigger, **_kwargs):
            result = trigger.emit_from_observation(
                observation=scan_observation[0],
                settlement_semantics=_Semantics(),
                decision_time=decision_time,
                received_at="2026-07-30T05:40:00+00:00",
            )
            return [], [] if result is None else [result]

        monkeypatch.setattr(reactor_module, "_edli_scan_day0_with_lock_retry", _scan)
        admission = reactor_module._Day0LiveFamilyAdmission(
            admitted_families=frozenset({admitted_family}),
            held_families=frozenset({admitted_family}),
            expiry_safe=True,
            scan_cities=frozenset({family[0]}),
        )
        first_event_ids = _edli_emit_day0_extreme_events(
            world,
            trade,
            decision_time=decision_time,
            received_at="2026-07-30T05:40:00+00:00",
            limit=5,
            family_admission=admission,
        )
        second_event_ids = _edli_emit_day0_extreme_events(
            world,
            trade,
            decision_time=decision_time,
            received_at="2026-07-30T05:40:00+00:00",
            limit=5,
            family_admission=admission,
        )

        bridged = []
        wakes = []
        monkeypatch.setattr(
            reactor_module,
            "_edli_bridge_day0_extreme_materialization_seeds",
            lambda event_ids: bridged.append(event_ids),
        )
        monkeypatch.setattr(
            reactor_wake,
            "publish_reactor_wake",
            lambda **kwargs: wakes.append(kwargs),
        )
        assert reactor_module._edli_publish_committed_day0_catchup(
            first_event_ids
        ) is True
        assert reactor_module._edli_publish_committed_day0_catchup(
            second_event_ids
        ) is False

        scan_observation[0] = {
            **refreshed_observation,
            "observation_time": "2026-07-30T05:45:00+00:00",
            "observation_available_at": "2026-07-30T05:50:00+00:00",
        }
        nonheld_event_ids = _edli_emit_day0_extreme_events(
            world,
            trade,
            decision_time=decision_time,
            received_at="2026-07-30T05:55:00+00:00",
            limit=5,
            family_admission=reactor_module._Day0LiveFamilyAdmission(
                admitted_families=frozenset({admitted_family}),
                expiry_safe=True,
                scan_cities=frozenset({family[0]}),
            ),
        )
        durable_event_count = world.execute(
            "SELECT COUNT(*) FROM opportunity_events "
            "WHERE event_type = 'DAY0_EXTREME_UPDATED'"
        ).fetchone()[0]
    finally:
        trade.close()
        world.close()

    assert len(first_event_ids) == 1
    assert second_event_ids == ()
    assert nonheld_event_ids == ()
    assert bridged == [first_event_ids]
    assert [wake["event_ids"] for wake in wakes] == [first_event_ids]
    assert durable_event_count == 2


def test_global_not_selected_is_terminal_for_completed_epoch(caplog):
    reason = "GLOBAL_NOT_SELECTED:winning-actuation-identity"

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert "GLOBAL_NOT_SELECTED" in TERMINAL_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is False

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


def test_global_no_reduce_only_family_is_terminal_for_completed_cut(caplog):
    reason = "GLOBAL_AUCTION_NO_REDUCE_ONLY_FAMILY"

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert reason in TERMINAL_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is False

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


def test_global_preflight_cash_is_terminal_only_for_complete_action_set(caplog):
    complete_cash = (
        "GLOBAL_PREFLIGHT_HOLD_CASH_OPTIMAL:"
        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:families=0:candidates=0"
    )
    candidate_missing = (
        "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:families=0:candidates=1"
    )

    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert "GLOBAL_PREFLIGHT_HOLD_CASH_OPTIMAL" in TERMINAL_MONEY_PATH_REASONS
        assert "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED" in TRANSIENT_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(complete_cash) is False
        assert _is_transient_money_path_reason(candidate_missing) is True

    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)


# A transient-REQUEUE test must price its decision at a VENUE-OPEN instant so the
# family is genuinely still tradeable (a fresh book is still capturable) and the
# venue-close horizon (reactor._venue_market_closed_horizon, 2026-06-13 zero-order
# reactor-stall fix) does NOT terminalize it. The _forecast_event fixture's snapshot
# becomes available at 2026-05-24T18:01Z, which is AFTER a 2026-05-24 market's
# 12:00Z venue close — so those requeue tests use a 2026-05-25 TARGET (closes 12:00Z
# 05-25) paired with this 05-25T06:10Z decision time (SETTLEMENT_DAY, pre-close,
# after the snapshot is available). The previous 18:10Z-on-05-24 only "requeued"
# because the reactor used to ignore the venue close until local-day-end.
_DT_VENUE_OPEN = datetime(2026, 5, 25, 6, 10, tzinfo=timezone.utc)


def _day0_event(key_suffix: str = "a"):
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        settlement_source="WU",
        station_id="KMDW",
        observation_time="2026-05-24T18:00:00+00:00",
        observation_available_at="2026-05-24T18:07:00+00:00",
        raw_value=74.2,
        rounded_value=74,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
    )
    return make_day0_extreme_updated_event(
        entity_key=f"Chicago|2026-05-24|high|{key_suffix}",
        source="day0_observation",
        observed_at=payload.observation_time,
        received_at="2026-05-24T18:08:00+00:00",
        payload=payload,
    )


def _day0_event_for_target(key_suffix: str, target_date: str, available_at: str):
    payload = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date=target_date,
        metric="high",
        settlement_source="WU",
        station_id="KMDW",
        observation_time=available_at,
        observation_available_at=available_at,
        raw_value=74.2,
        rounded_value=74,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
    )
    return make_day0_extreme_updated_event(
        entity_key=f"Chicago|{target_date}|high|{key_suffix}",
        source="day0_observation",
        observed_at=payload.observation_time,
        received_at=available_at,
        payload=payload,
    )


def _forecast_event(key_suffix: str = "a", target_date: str = "2026-05-24"):
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date=target_date,
        metric="high",
        source_id="opendata",
        source_run_id="run-1",
        cycle="00",
        track="live",
        snapshot_id="snap-1",
        snapshot_hash="hash-1",
        captured_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0],
        observed_steps=[0],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|{target_date}|high|{key_suffix}",
        source="forecast_live",
        observed_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
        received_at="2026-05-24T18:02:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-1",
    )


def _market_event():
    payload = MarketBookEventPayload(
        condition_id="0xcondition",
        token_id="token-1",
        outcome_label="YES",
        event_type="BOOK_SNAPSHOT",
        quote_seen_at="2026-05-24T18:07:00+00:00",
        book_hash="hash-1",
    )
    return make_opportunity_event(
        event_type="BOOK_SNAPSHOT",
        entity_key="0xcondition|token-1",
        source="polymarket_market_channel",
        observed_at=payload.quote_seen_at,
        available_at=payload.quote_seen_at,
        received_at="2026-05-24T18:08:00+00:00",
        payload=payload,
        causal_snapshot_id="hash-1",
    )


def _current_pre_submit_proof_bundle(
    event,
    receipt: EventSubmissionReceipt,
    *,
    decision_time: datetime,
) -> PreSubmitProofBundle:
    """Build the smallest typed current-semantic proof accepted by the compiler."""

    decision_time = decision_time.astimezone(timezone.utc)
    event_payload = json.loads(event.payload_json)
    event_clock = EvidenceClock(
        datetime.fromisoformat(event.available_at),
        datetime.fromisoformat(event.received_at),
        datetime.fromisoformat(event.created_at),
    )
    decision_clock = EvidenceClock(decision_time, decision_time, decision_time)
    family_id = str(receipt.family_id or "family-1")
    condition_id = str(receipt.condition_id or "condition-1")
    token_id = str(receipt.token_id or "yes-1")
    snapshot_id = str(receipt.executable_snapshot_id or "snapshot-exec-1")
    direction = "buy_yes"
    final_intent_id = str(receipt.final_intent_id or f"edli_intent:{event.event_id}:{token_id}")
    cost_basis_id = str(receipt.kelly_cost_basis_id or "cost-1")
    bin_labels_hash = stable_hash(("70-71F",))
    members_json_hash = stable_hash(tuple([70.5] * 51))
    model_hash = stable_hash({"model": "test-current"})
    model_config_hash = stable_hash({"edge_bootstrap_n": 1000})
    p_cal_hash = stable_hash((0.8, 0.2))
    p_live_hash = stable_hash((0.78, 0.22))
    source_payload = {
        "identity": event.source,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "event_source": event.source,
        "source_status": "LIVE_ELIGIBLE",
        "source_authority_id": "read_executable_forecast",
        "source_reason_code": None,
        "derived_from_certificate_type": claims.FORECAST_AUTHORITY,
        "derived_from_snapshot_id": event.causal_snapshot_id,
        "derived_from_source_run_id": event_payload.get("source_run_id"),
        "derived_from_reader_status": "LIVE_ELIGIBLE",
        "causal_snapshot_id": event.causal_snapshot_id,
        "snapshot_id": event.causal_snapshot_id,
        "completeness_status": event_payload.get("completeness_status"),
        "required_fields_present": event_payload.get("required_fields_present"),
        "required_steps_present": event_payload.get("required_steps_present"),
        "source_id": event_payload.get("source_id"),
        "source_run_id": event_payload.get("source_run_id"),
        "payload_hash": event.payload_hash,
        "available_at": event.available_at,
        "received_at": event.received_at,
    }
    forecast_payload = {
        "identity": event.causal_snapshot_id,
        "snapshot_id": event.causal_snapshot_id,
        "reader_authority": "read_executable_forecast",
        "temperature_metric": event_payload.get("metric"),
        "members_extrema_metric_identity": event_payload.get("metric"),
        "members_extrema_transform": "daily_max",
        "members_json_source": ENSEMBLE_MEMBERS_JSON_SOURCE,
        "members_json_hash": members_json_hash,
        "target_local_date": event_payload.get("target_date"),
        "city_timezone": "America/Chicago",
        "bin_labels_hash": bin_labels_hash,
        "unit": "F",
        "settlement_unit": "F",
        "members_unit": "degF",
        "unit_authority_source": "ensemble_snapshots.settlement_unit",
        "local_date_window_hash": "window-hash-1",
        "forecast_source_id": event_payload.get("source_id"),
        "source_run_id": event_payload.get("source_run_id"),
        "source_cycle_time": "2026-05-24T00:00:00+00:00",
        "horizon_profile": "default",
        "reader_status": "LIVE_ELIGIBLE",
        "reader_reason_code": None,
        "coverage_readiness_status": "LIVE_ELIGIBLE",
        "coverage_completeness_status": "COMPLETE",
        "source_run_completeness_status": "COMPLETE",
        "source_run_status": "SUCCESS",
        "required_steps": (0,),
        "observed_steps": (0,),
        "expected_members": 51,
        "observed_members": 51,
        "applied_validations": (
            "source_run_completeness_status",
            "coverage_completeness_status",
            "coverage_readiness_status",
            "required_steps_observed",
            "expected_members_observed",
            "causality_status_ok",
            "authority_verified",
            "available_at_not_future",
        ),
    }
    topology_payload = {"identity": family_id, "family_id": family_id, "condition_ids": (condition_id,)}
    family_payload = {
        "identity": family_id,
        "family_id": family_id,
        "condition_ids": (condition_id,),
        "bin_labels_hash": bin_labels_hash,
        "bin_units": ("F",),
        "metric": event_payload.get("metric"),
        "target_date": event_payload.get("target_date"),
    }
    calibration_payload = {
        "identity": "model-1",
        "calibrator_model_key": "model-1",
        "raw_source_id": event_payload.get("source_id"),
        "source_cycle": "00",
        "horizon_profile": "default",
        "model_hash": model_hash,
        "authority": "VERIFIED",
        "maturity_level": 1,
        "input_space": "width_normalized_density",
        "training_cutoff": "2026-05-01T00:00:00+00:00",
        "model_available_at": "2026-05-01T00:00:00+00:00",
    }
    model_config_payload = {
        "identity": "edli_v1",
        "edge_bootstrap_n": 1000,
        "market_analysis_config_hash": model_config_hash,
        "calibration_input_space": "width_normalized_density",
        "calibrator_model_key": "model-1",
        "calibrator_model_hash": model_hash,
    }
    belief_payload = {
        "identity": f"{family_id}:{token_id}",
        "forecast_snapshot_id": event.causal_snapshot_id,
        "calibrator_model_key": "model-1",
        "calibrator_model_hash": model_hash,
        "bin_labels_hash": bin_labels_hash,
        "p_cal_vector_hash": p_cal_hash,
        "p_live_vector_hash": p_live_hash,
        "p_cal_hash": p_cal_hash,
        "p_live_hash": p_live_hash,
        "market_analysis_config_hash": model_config_hash,
        "bootstrap_n": 1000,
        "members_json_hash": members_json_hash,
        "unit": "F",
        "unit_authority_source": "ensemble_snapshots.settlement_unit",
    }
    executable_payload = {
        "identity": snapshot_id,
        "selected_snapshot_id": snapshot_id,
        "condition_id": condition_id,
        "token_id": token_id,
        "executable_snapshot_hash": stable_hash((snapshot_id, condition_id, token_id, direction)),
        "min_tick_size": "0.01",
        "min_order_size": "1",
        "market_end_at": (decision_time + timedelta(hours=12)).isoformat(),
    }
    quote_payload = {
        "identity": f"{family_id}:{token_id}",
        "condition_id": condition_id,
        "token_id": token_id,
        "selected_token_id": token_id,
        "direction": direction,
        "native_side": "YES_ASK",
        "cost_source": "native_orderbook_ask",
        "quote_source_kind": "executable_market_snapshot_native_book",
        "forbidden_cost_source": False,
        "execution_price_type": "ExecutionPrice",
        "best_bid": 0.39,
        "best_ask": 0.41,
        "book_hash": stable_hash((snapshot_id, condition_id, token_id, 0.39, 0.41)),
    }
    cost_payload = {
        "identity": cost_basis_id,
        "cost_basis_id": cost_basis_id,
        "condition_id": condition_id,
        "token_id": token_id,
        "cost_source": "native_orderbook_ask",
        "quote_source_kind": "executable_market_snapshot_native_book",
        "forbidden_cost_source": False,
        "execution_price_type": "ExecutionPrice",
        "cost_basis_hash": stable_hash((cost_basis_id, condition_id, token_id, direction)),
    }
    pre_trade_payload = {
        "identity": f"{family_id}:{token_id}",
        "quote_edge_bound": 0.1,
        "conditional_edge_given_fill": 0.1,
        "actionable_trade_score": 0.0,
    }
    candidate_payload = {
        "identity": f"{family_id}:{token_id}",
        "candidate_id": "candidate-1",
        "family_id": family_id,
        "condition_id": condition_id,
        "selected_token_id": token_id,
        "direction": direction,
        "hypothesis_id": f"{family_id}:{token_id}",
    }
    protocol_payload = {"identity": family_id, "testing_protocol_id": f"test:{family_id}", "family_id": family_id}
    fdr_payload = {
        "identity": family_id,
        "fdr_family_id": family_id,
        "selected_hypotheses": (f"{family_id}:{token_id}",),
        "fdr_hypothesis_count": 2,
        "edge_bootstrap_n": 1000,
    }
    sizing_payload = {
        "identity": "kelly-1",
        "kelly_decision_id": "kelly-1",
        "cost_basis_id": cost_basis_id,
        "execution_price_type": "ExecutionPrice",
        "passed": True,
    }
    risk_payload = {
        "identity": "risk-1",
        "risk_decision_id": "risk-1",
        "risk_level": "GREEN",
        "final_intent_id": final_intent_id,
        "passed": True,
    }

    def evidence(certificate_type, claim_type, payload, clock, authority_id):
        return AuthorityEvidence(certificate_type, claim_type, claim_type, payload, clock, authority_id)

    projection = {
        "event_id": event.event_id,
        "final_intent_id": final_intent_id,
        "side_effect_status": "NO_SUBMIT",
        "proof_accepted": True,
        "submitted": False,
        "executable_snapshot_id": snapshot_id,
    }
    projection["projection_hash"] = stable_hash(projection)
    return PreSubmitProofBundle(
        final_intent_id=final_intent_id,
        source_truth=evidence(claims.SOURCE_TRUTH, "source_truth", source_payload, event_clock, "test.source_truth"),
        market_topology=evidence(claims.MARKET_TOPOLOGY, "market_topology", topology_payload, decision_clock, "test.market_topology"),
        family_closure=evidence(claims.FAMILY_CLOSURE, "family_closure", family_payload, decision_clock, "test.family_closure"),
        forecast_authority=evidence(claims.FORECAST_AUTHORITY, "forecast_authority", forecast_payload, event_clock, "test.forecast_authority"),
        calibration=evidence(claims.CALIBRATION, "calibration", calibration_payload, decision_clock, "test.calibration"),
        model_config=evidence(claims.MODEL_CONFIG, "model_config", model_config_payload, decision_clock, "test.model_config"),
        belief=evidence(claims.BELIEF, "belief", belief_payload, decision_clock, "test.belief"),
        executable_snapshot=evidence(claims.EXECUTABLE_SNAPSHOT, "executable_snapshot", executable_payload, decision_clock, "test.executable_snapshot"),
        quote_feasibility=evidence(claims.QUOTE_FEASIBILITY, "quote_feasibility", quote_payload, decision_clock, "test.quote_feasibility"),
        cost_model=evidence(claims.COST_MODEL, "cost_model", cost_payload, decision_clock, "test.cost_model"),
        pre_trade_evidence=evidence(claims.PRE_TRADE_EVIDENCE, "pre_trade_evidence", pre_trade_payload, decision_clock, "test.pre_trade_evidence"),
        candidate_evidence=evidence(claims.CANDIDATE_EVIDENCE, "candidate_evidence", candidate_payload, decision_clock, "test.candidate_evidence"),
        testing_protocol=evidence(claims.TESTING_PROTOCOL, "testing_protocol", protocol_payload, decision_clock, "test.testing_protocol"),
        fdr=evidence(claims.FDR, "fdr", fdr_payload, decision_clock, "test.fdr"),
        sizing=evidence(claims.SIZING, "sizing", sizing_payload, decision_clock, "test.sizing"),
        risk_level=evidence(claims.RISK_LEVEL, "risk_level", risk_payload, decision_clock, "test.risk"),
        pre_submit_projection=projection,
    )


def test_forecast_wake_events_follow_posterior_reversal_order():
    paris = SimpleNamespace(
        event_id="paris",
        payload_json=json.dumps(
            {"city": "Paris", "target_date": "2026-07-18", "metric": "high"}
        ),
    )
    shanghai = SimpleNamespace(
        event_id="shanghai",
        payload_json=json.dumps(
            {"city": "Shanghai", "target_date": "2026-07-18", "metric": "high"}
        ),
    )
    ordinary = SimpleNamespace(
        event_id="ordinary",
        payload_json=json.dumps(
            {"city": "London", "target_date": "2026-07-18", "metric": "high"}
        ),
    )

    ranked = _rank_forecast_wake_events(
        [ordinary, paris, shanghai],
        [
            ("Shanghai", "2026-07-18", "high"),
            ("Paris", "2026-07-18", "high"),
        ],
    )

    assert [event.event_id for event in ranked] == [
        "shanghai",
        "paris",
        "ordinary",
    ]


def _reactor(store, *, gates=True, config=None):
    rejected = []
    submitted = []
    def _submit(event, _decision_time):
        payload = json.loads(event.payload_json)
        submitted.append(event.event_id)
        receipt = EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1",
            family_id="family-1",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id=f"edli_intent:{event.event_id}:yes-1",
            submit_lane="LIVE_PRE_VENUE_ABORT",
            reason="SUBMIT_ABORTED_ENTRY_PRICE_BELOW_STRATEGY_FLOOR",
        )
        return replace(
            receipt,
            decision_proof_bundle=_current_pre_submit_proof_bundle(
                event,
                receipt,
                decision_time=_decision_time,
            ),
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: gates,
        executable_snapshot_gate=lambda _event, _decision_time: gates,
        riskguard_gate=lambda _event: gates,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=config or ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    return reactor, rejected, submitted


def test_cycle_entry_gate_stops_before_pending_event_work():
    conn, store = _store()
    events = (
        _forecast_event(key_suffix="risk-blocked-a"),
        _forecast_event(key_suffix="risk-blocked-b"),
    )
    for event in events:
        store.insert_or_ignore(event)
    per_event_gate_calls = []
    cycle_gate_calls = []

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: pytest.fail(
            "source gate must not run while cycle entry is blocked"
        ),
        executable_snapshot_gate=lambda _event, _decision_time: pytest.fail(
            "snapshot gate must not run while cycle entry is blocked"
        ),
        riskguard_gate=lambda event: per_event_gate_calls.append(event.event_id)
        or False,
        cycle_entry_gate=lambda: cycle_gate_calls.append(True) or False,
        final_intent_submit=lambda *_args: pytest.fail(
            "submit must not run while cycle entry is blocked"
        ),
        reject=lambda *_args: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.rejection_reasons == ["RISK_GUARD_BLOCKED"]
    assert cycle_gate_calls == [True]
    assert per_event_gate_calls == []
    assert [_processing_status(conn, event.event_id) for event in events] == [
        "pending",
        "pending",
    ]


def test_blocked_entry_cycle_returns_before_runtime_db_setup(monkeypatch):
    import src.main as main
    import src.state.db as db
    from src.events.reactor import run_edli_event_reactor_cycle
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime import reactor_wake

    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda *_args, **_kwargs: {
            "enabled": True,
            "event_writer_enabled": True,
        },
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(reactor_wake, "reactor_urgent_wake_revision", lambda: None)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.YELLOW)
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda: pytest.fail("blocked entry cycle must not open the world DB"),
    )

    assert run_edli_event_reactor_cycle(active_lock=threading.Lock()) is True


def test_blocked_entry_cycle_keeps_untyped_held_sell_completion_wake(monkeypatch):
    import src.main as main
    import src.state.db as db
    from src.events.reactor import run_edli_event_reactor_cycle
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime import reactor_wake

    monkeypatch.setattr(main, "_settings_section", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        main,
        "_defer_for_held_position_monitor",
        lambda _job: False,
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_revision",
        lambda: None,
    )
    monkeypatch.setattr(
        riskguard,
        "get_current_level",
        lambda: RiskLevel.YELLOW,
    )
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda: pytest.fail("completion wake must stay durable while blocked"),
    )

    assert (
        run_edli_event_reactor_cycle(
            active_lock=threading.Lock(),
            producer_wake_reason=(
                "held_sell_global_auction_completion_requested"
            ),
        )
        is False
    )


@pytest.mark.parametrize("risk_level_name", ("YELLOW", "ORANGE", "RED"))
def test_blocked_entry_cycle_still_runs_typed_held_sell_completion(
    monkeypatch,
    risk_level_name,
):
    """Entry risk posture must not disable reduce-only global exit selection."""

    import src.main as main
    import src.state.db as db
    from src.events.reactor import run_edli_event_reactor_cycle
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime import reactor_wake

    class ExitAuctionReached(RuntimeError):
        pass

    monkeypatch.setattr(main, "_settings_section", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        main,
        "_defer_for_held_position_monitor",
        lambda _job: False,
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_revision",
        lambda: None,
    )
    monkeypatch.setattr(
        riskguard,
        "get_current_level",
        lambda: getattr(RiskLevel, risk_level_name),
    )
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda: (_ for _ in ()).throw(ExitAuctionReached()),
    )

    with pytest.raises(ExitAuctionReached):
        run_edli_event_reactor_cycle(
            active_lock=threading.Lock(),
            producer_wake_reason=(
                "held_sell_global_auction_completion_requested"
            ),
            producer_held_sell_reauction_requests=(object(),),
        )


def test_periodic_cycle_yields_to_already_pending_day0_before_runtime_db_setup(
    monkeypatch,
):
    import src.main as main
    import src.state.db as db
    from src.events.reactor import run_edli_event_reactor_cycle
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime import reactor_wake

    class _Lock:
        acquired = False

        def locked(self):
            return False

        def acquire(self, *, blocking):
            assert blocking is False
            self.acquired = True
            return True

        def release(self):
            self.acquired = False

    lock = _Lock()
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda *_args, **_kwargs: {
            "enabled": True,
            "event_writer_enabled": True,
        },
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(reactor_wake, "reactor_urgent_wake_revision", lambda: "day0-wake")
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda: pytest.fail("periodic reactor must yield before opening the world DB"),
    )

    assert run_edli_event_reactor_cycle(
        active_lock=lock,
        urgent_day0_pending=lambda: True,
    ) is False
    assert lock.acquired is False


def test_cycle_releases_dispatcher_lock_when_connection_close_fails(monkeypatch):
    import src.main as main
    import src.state.db as db
    from src.events.reactor import run_edli_event_reactor_cycle
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime import reactor_wake

    class _Rows:
        def fetchall(self):
            return [(0, "main", "")]

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Rows()

        def close(self):
            raise RuntimeError("connection close failed")

    class _ExplodingMutex:
        def acquire(self, **_kwargs):
            raise RuntimeError("cycle setup failed")

    lock = threading.Lock()
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda *_args, **_kwargs: {
            "enabled": True,
            "event_writer_enabled": True,
            "forecast_snapshot_trigger_enabled": False,
            "day0_extreme_trigger_enabled": False,
        },
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(
        main,
        "_start_venue_background_maintenance_after_reactor_if_required",
        lambda: None,
    )
    monkeypatch.setattr(reactor_wake, "reactor_urgent_wake_revision", lambda: None)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(db, "get_world_connection", _Conn)
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", _Conn)
    monkeypatch.setattr(db, "world_write_mutex", lambda: _ExplodingMutex())

    with pytest.raises(RuntimeError, match="connection close failed"):
        run_edli_event_reactor_cycle(active_lock=lock)

    assert lock.locked() is False


@pytest.mark.parametrize("failing_step", ["urgent_wake", "monitor_handoff"])
def test_cycle_releases_dispatcher_lock_when_pre_setup_check_fails(
    monkeypatch,
    failing_step,
):
    import src.main as main
    import src.state.db as db
    from src.events.reactor import run_edli_event_reactor_cycle
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime import reactor_wake

    def _fail():
        raise RuntimeError(f"{failing_step} failed")

    monitor_calls = 0

    def _monitor_handoff(_job):
        nonlocal monitor_calls
        monitor_calls += 1
        if failing_step == "monitor_handoff" and monitor_calls == 2:
            _fail()
        return False

    lock = threading.Lock()
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda *_args, **_kwargs: {
            "enabled": True,
            "event_writer_enabled": True,
        },
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", _monitor_handoff)
    monkeypatch.setattr(reactor_wake, "reactor_urgent_wake_revision", lambda: None)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda: pytest.fail("pre-setup failure must not open the world DB"),
    )

    urgent_check = _fail if failing_step == "urgent_wake" else lambda: False
    with pytest.raises(RuntimeError, match=f"{failing_step} failed"):
        run_edli_event_reactor_cycle(
            active_lock=lock,
            urgent_day0_pending=urgent_check,
        )

    assert lock.locked() is False


def test_cycle_releases_dispatcher_lock_when_forecast_open_and_world_close_fail(
    monkeypatch,
):
    import src.main as main
    import src.state.db as db
    from src.events.reactor import run_edli_event_reactor_cycle
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime import reactor_wake

    class _Rows:
        def fetchall(self):
            return [(0, "main", "")]

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Rows()

        def close(self):
            raise RuntimeError("world close failed")

    lock = threading.Lock()
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda *_args, **_kwargs: {
            "enabled": True,
            "event_writer_enabled": True,
        },
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(reactor_wake, "reactor_urgent_wake_revision", lambda: None)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(db, "get_world_connection", _Conn)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda: (_ for _ in ()).throw(RuntimeError("forecast open failed")),
    )

    with pytest.raises(RuntimeError, match="world close failed"):
        run_edli_event_reactor_cycle(active_lock=lock)

    assert lock.locked() is False


def test_targeted_forecast_wake_ignores_disjoint_family_revision(monkeypatch):
    from src.events.reactor import _reactor_wake_cancellation_probe
    from src.runtime import reactor_wake

    current = reactor_wake.ReactorWake(
        "wake-current",
        "2026-07-19T12:00:00+00:00",
        "forecast",
        "forecast_posterior_advanced",
        forecast_families=(("Paris", "2026-07-20", "high"),),
    )
    pending = reactor_wake.ReactorWake(
        "wake-pending",
        "2026-07-19T12:00:01+00:00",
        "forecast",
        "forecast_posterior_advanced",
        forecast_families=(("Shanghai", "2026-07-20", "high"),),
    )
    revisions = iter(("base", "new", "new"))
    reads = []
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_revision",
        lambda: next(revisions),
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda _published_at, *, exclude_wake_ids=(): reads.append(
            frozenset(exclude_wake_ids)
        )
        or (pending,),
    )

    cancelled = _reactor_wake_cancellation_probe(
        producer_wake_reason="forecast_posterior_advanced",
        producer_wake_ids=(current.wake_id,),
        producer_wake_published_at=current.published_at,
        forecast_wake_families=set(current.forecast_families),
        urgent_day0_pending=None,
    )

    assert cancelled() is False
    assert cancelled() is False
    assert reads == [frozenset({current.wake_id})]


def test_day0_posterior_advance_reemits_current_observation_on_new_probability_clock():
    conn, _store_obj = _store()
    observation = Day0ExtremeUpdatedPayload(
        city="Chicago",
        target_date="2026-07-28",
        metric="high",
        settlement_source="weather_underground",
        station_id="KORD",
        observation_time="2026-07-28T12:00:00+00:00",
        observation_available_at="2026-07-28T12:00:05+00:00",
        raw_value=30.0,
        rounded_value=30,
        high_so_far=30.0,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
    )
    prior = make_day0_extreme_updated_event(
        entity_key="Chicago|2026-07-28|high|KORD",
        source="day0_extreme_updated_trigger",
        observed_at=observation.observation_time,
        received_at="2026-07-28T12:00:06+00:00",
        payload=observation,
        causal_snapshot_id="observation-context",
    )
    EventStore(conn).insert_or_ignore(prior)
    conn.commit()
    posterior = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-07-28|high|posterior-42",
        source="cycle-test-1",
        observed_at="2026-07-28T12:09:55+00:00",
        available_at="2026-07-28T12:09:56+00:00",
        received_at="2026-07-28T12:09:57+00:00",
        payload={
            "city": "Chicago",
            "target_date": "2026-07-28",
            "metric": "high",
        },
        causal_snapshot_id="posterior-42",
    )

    events = _build_day0_posterior_redecision_events(
        conn,
        (posterior,),
        day0_families={("Chicago", "2026-07-28", "high")},
        received_at="2026-07-28T12:09:57+00:00",
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "DAY0_EXTREME_UPDATED"
    assert event.available_at == posterior.available_at
    assert event.causal_snapshot_id == "posterior-42"
    payload = json.loads(event.payload_json)
    assert payload["observation_available_at"] == observation.observation_available_at
    assert payload["posterior_redecision_identity"] == "posterior-42"


def test_reactor_wake_oldest_joint_input_prevents_forecast_starvation(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="forecast",
        reason="forecast_posterior_advanced",
        path=path,
        wake_id="forecast-old",
        published_at=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
        forecast_families=(("Paris", "2026-07-26", "high"),),
    )
    reactor_wake.publish_reactor_wake(
        source="forecast",
        reason="forecast_posterior_advanced",
        path=path,
        wake_id="forecast-current",
        published_at=datetime(2026, 7, 25, 12, 0, 1, tzinfo=timezone.utc),
        forecast_families=(("London", "2026-07-26", "high"),),
    )
    reactor_wake.publish_reactor_wake(
        source="price",
        reason="market_price_advanced",
        path=path,
        wake_id="price-newer",
        published_at=datetime(2026, 7, 25, 12, 0, 2, tzinfo=timezone.utc),
    )

    selected = reactor_wake.read_reactor_wake(path=path)

    assert selected is not None
    assert selected.wake_id == "forecast-current"


def test_reactor_wake_preserves_price_fast_path_when_price_is_older(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="price",
        reason="market_price_advanced",
        path=path,
        wake_id="price-old",
        published_at=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
    )
    reactor_wake.publish_reactor_wake(
        source="forecast",
        reason="forecast_posterior_advanced",
        path=path,
        wake_id="forecast-newer",
        published_at=datetime(2026, 7, 25, 12, 0, 1, tzinfo=timezone.utc),
        forecast_families=(("Paris", "2026-07-26", "high"),),
    )

    selected = reactor_wake.read_reactor_wake(path=path)

    assert selected is not None
    assert selected.wake_id == "price-old"


def test_reactor_wake_day0_still_preempts_older_joint_inputs(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="forecast",
        reason="forecast_posterior_advanced",
        path=path,
        wake_id="forecast-old",
        published_at=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
        forecast_families=(("Paris", "2026-07-26", "high"),),
    )
    reactor_wake.publish_reactor_wake(
        source="day0",
        reason="day0_extreme_event_committed",
        path=path,
        wake_id="day0-new",
        published_at=datetime(2026, 7, 25, 12, 0, 1, tzinfo=timezone.utc),
    )
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="completion-newer",
        published_at=datetime(2026, 7, 25, 12, 0, 1, 500000, tzinfo=timezone.utc),
    )
    reactor_wake.publish_reactor_wake(
        source="fill",
        reason="position_fill_projected",
        path=path,
        wake_id="fill-newest",
        published_at=datetime(2026, 7, 25, 12, 0, 2, tzinfo=timezone.utc),
        event_ids=("fill-event",),
    )

    selected = reactor_wake.read_reactor_wake(path=path)

    assert selected is not None
    assert selected.wake_id == "day0-new"


def test_expired_exact_held_sell_deadline_preempts_day0(tmp_path):
    from src.runtime import reactor_wake

    def selected_reason(path, deadline):
        request = reactor_wake.make_held_sell_reauction_request(
            position_id=f"held-{path.name}",
            family=("Paris", "2026-08-13", "high"),
            probability_content_identity="q-current",
            held_token_id=f"token-{path.name}",
            held_best_bid=0.11,
            bid_observed_at="2026-08-12T12:00:00+00:00",
            schema_version=4,
            completion_deadline_at=deadline,
        )
        exact = reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=path,
            wake_id=f"exact-{path.name}",
            held_sell_reauction_requests=(request,),
        )
        day0 = reactor_wake.publish_reactor_wake(
            source="day0",
            reason="day0_extreme_event_committed",
            path=path,
            wake_id=f"day0-{path.name}",
        )
        return reactor_wake.read_reactor_wake(path=path), exact, day0

    future_selected, _future_exact, future_day0 = selected_reason(
        tmp_path / "future.json",
        "2099-01-01T00:00:00+00:00",
    )
    expired_selected, expired_exact, _expired_day0 = selected_reason(
        tmp_path / "expired.json",
        "2000-01-01T00:00:00+00:00",
    )

    assert future_selected == future_day0
    assert expired_selected == expired_exact
    assert expired_selected.held_sell_reauction_requests[0].held_best_bid == 0.11
    assert (
        expired_selected.held_sell_reauction_requests[0].completion_deadline_at
        == "2000-01-01T00:00:00+00:00"
    )


def test_post_terminal_day0_cleanup_gives_one_turn_to_material_input(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="price",
        reason="market_price_advanced",
        path=path,
        wake_id="price-old",
        published_at=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
    )
    reactor_wake.publish_reactor_wake(
        source="day0",
        reason="day0_extreme_event_committed",
        path=path,
        wake_id="day0-new",
        published_at=datetime(2026, 7, 25, 12, 0, 1, tzinfo=timezone.utc),
    )

    selected = reactor_wake.read_reactor_wake(
        path=path,
        prefer_material_progress=True,
    )

    assert selected is not None
    assert selected.wake_id == "price-old"

    reactor_wake.publish_reactor_wake(
        source="fill",
        reason="position_fill_projected",
        path=path,
        wake_id="fill-newest",
        published_at=datetime(2026, 7, 25, 12, 0, 2, tzinfo=timezone.utc),
        event_ids=("fill-event",),
    )
    selected = reactor_wake.read_reactor_wake(
        path=path,
        prefer_material_progress=True,
    )
    assert selected is not None
    assert selected.wake_id == "fill-newest"


def test_reactor_wake_fill_is_bounded_fair_with_continuous_joint_inputs(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="price",
        reason="market_price_advanced",
        path=path,
        wake_id="price-old",
        published_at=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
    )
    reactor_wake.publish_reactor_wake(
        source="fill",
        reason="position_fill_projected",
        path=path,
        wake_id="fill-first",
        published_at=datetime(2026, 7, 25, 12, 0, 1, tzinfo=timezone.utc),
        event_ids=("fill-event-first",),
    )
    reactor_wake.publish_reactor_wake(
        source="forecast",
        reason="forecast_posterior_advanced",
        path=path,
        wake_id="forecast-new",
        published_at=datetime(2026, 7, 25, 12, 0, 2, tzinfo=timezone.utc),
        forecast_families=(("Paris", "2026-07-26", "high"),),
    )
    reactor_wake.publish_reactor_wake(
        source="fill",
        reason="position_fill_projected",
        path=path,
        wake_id="fill-second",
        published_at=datetime(2026, 7, 25, 12, 0, 3, tzinfo=timezone.utc),
        event_ids=("fill-event-second",),
    )

    first = reactor_wake.read_reactor_wake(path=path)
    assert first is not None
    assert first.wake_id == "price-old"
    assert reactor_wake.acknowledge_reactor_wake(first, path=path)

    reactor_wake.publish_reactor_wake(
        source="fill",
        reason="position_fill_projected",
        path=path,
        wake_id="fill-later",
        published_at=datetime(2026, 7, 25, 12, 0, 4, tzinfo=timezone.utc),
        event_ids=("fill-event-later",),
    )

    second = reactor_wake.read_reactor_wake(path=path)
    assert second is not None
    assert second.wake_id == "fill-first"
    assert "position_fill_projected" in reactor_wake.URGENT_WAKE_REASONS
    fill_batch = reactor_wake.coalescible_reactor_wakes(second, path=path)
    assert tuple(wake.wake_id for wake in fill_batch) == (
        "fill-first",
        "fill-second",
        "fill-later",
    )
    assert reactor_wake.acknowledge_reactor_wakes(fill_batch, path=path)

    reactor_wake.publish_reactor_wake(
        source="fill",
        reason="position_fill_projected",
        path=path,
        wake_id="fill-newest",
        published_at=datetime(2026, 7, 25, 12, 0, 5, tzinfo=timezone.utc),
        event_ids=("fill-event-newest",),
    )

    third = reactor_wake.read_reactor_wake(path=path)
    assert third is not None
    assert third.wake_id == "forecast-new"


def test_reactor_wake_priority_quadrants_keep_fresh_material_ahead_of_generic(
    tmp_path,
):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    generic = reactor_wake.publish_reactor_wake(
        source="periodic_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="completion-generic",
        published_at=datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
    )
    forecast = reactor_wake.publish_reactor_wake(
        source="forecast",
        reason="forecast_posterior_advanced",
        path=path,
        wake_id="forecast-fresh",
        published_at=datetime(2026, 7, 28, 8, 0, 1, tzinfo=timezone.utc),
        forecast_families=(("Paris", "2026-07-28", "high"),),
    )
    request = reactor_wake.make_held_sell_reauction_request(
        position_id="position-capital-at-risk",
        family=("Paris", "2026-07-28", "high"),
        probability_content_identity="q-current",
        held_token_id="token-held",
        held_best_bid=0.11,
        bid_observed_at="2026-07-28T08:00:02+00:00",
    )
    exact = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="completion-exact",
        published_at=datetime(2026, 7, 28, 8, 0, 2, tzinfo=timezone.utc),
        forecast_families=(request.family,),
        held_sell_reauction_requests=(request,),
    )
    day0 = reactor_wake.publish_reactor_wake(
        source="day0",
        reason="day0_extreme_event_committed",
        path=path,
        wake_id="day0-current",
        published_at=datetime(2026, 7, 28, 8, 0, 3, tzinfo=timezone.utc),
    )

    selected = reactor_wake.read_reactor_wake(path=path)
    assert selected is not None
    assert selected == day0  # Day0 > exact completion > material > generic.
    assert reactor_wake.acknowledge_reactor_wake(selected, path=path)

    selected = reactor_wake.read_reactor_wake(path=path)
    assert selected == exact
    assert reactor_wake.acknowledge_reactor_wake(selected, path=path)

    selected = reactor_wake.read_reactor_wake(path=path)
    assert selected == forecast
    assert reactor_wake.acknowledge_reactor_wake(selected, path=path)

    selected = reactor_wake.read_reactor_wake(path=path)
    assert selected == generic


def test_paused_forecast_carrier_priority_preserves_fill_and_exact_held_priority(tmp_path):
    """The paused carrier preference cannot outrank capital-at-risk wakes."""

    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    forecast = reactor_wake.publish_reactor_wake(
        source="forecast",
        reason="forecast_posterior_advanced",
        path=path,
        wake_id="forecast-future",
        published_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        forecast_families=(("Paris", "2026-08-03", "high"),),
    )
    day0 = reactor_wake.publish_reactor_wake(
        source="day0",
        reason="day0_extreme_event_committed",
        path=path,
        wake_id="day0-pure-entry",
        published_at=datetime(2026, 8, 2, 12, 0, 1, tzinfo=timezone.utc),
    )

    assert reactor_wake.read_reactor_wake(path=path) == day0
    assert (
        reactor_wake.read_reactor_wake(
            path=path,
            prefer_forecast_carrier_progress=True,
        )
        == forecast
    )

    fill = reactor_wake.publish_reactor_wake(
        source="fill",
        reason="position_fill_projected",
        path=path,
        wake_id="fill-urgent",
        published_at=datetime(2026, 8, 2, 12, 0, 2, tzinfo=timezone.utc),
        event_ids=("fill-event",),
    )
    assert (
        reactor_wake.read_reactor_wake(
            path=path,
            prefer_forecast_carrier_progress=True,
        )
        == fill
    )

    request = reactor_wake.make_held_sell_reauction_request(
        position_id="held-position",
        family=("Paris", "2026-08-02", "high"),
        probability_content_identity="q-held",
        held_token_id="held-token",
        held_best_bid=0.12,
        bid_observed_at="2026-08-02T12:00:03+00:00",
    )
    exact = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="exact-held-sell",
        published_at=datetime(2026, 8, 2, 12, 0, 3, tzinfo=timezone.utc),
        held_sell_reauction_requests=(request,),
    )
    assert (
        reactor_wake.read_reactor_wake(
            path=path,
            prefer_forecast_carrier_progress=True,
        )
        == exact
    )


def test_paused_forecast_carrier_priority_requires_pause_and_serviced_exposure(monkeypatch):
    import src.control.control_plane as control_plane
    import src.main as main

    pause_state = [{"status": "ok", "entries_paused": True}]
    exposure = [0]

    monkeypatch.setattr(
        control_plane,
        "_refresh_entries_pause_from_durable_state",
        lambda: pause_state[0],
    )
    monkeypatch.setattr(
        main,
        "_current_periodic_monitor_obligation_count",
        lambda: exposure[0],
    )

    assert main._paused_forecast_carrier_priority_allowed() is True
    exposure[0] = 1
    assert main._paused_forecast_carrier_priority_allowed() is False
    assert (
        main._paused_forecast_carrier_priority_allowed(
            exposure_priority_served=True,
        )
        is True
    )
    exposure[0] = None
    assert main._paused_forecast_carrier_priority_allowed() is False

    def unreadable_exposure():
        raise OSError("canonical exposure read failed")

    monkeypatch.setattr(
        main,
        "_current_periodic_monitor_obligation_count",
        unreadable_exposure,
    )
    assert main._paused_forecast_carrier_priority_allowed() is False

    pause_state[0] = {"status": "ok", "entries_paused": False}
    assert main._paused_forecast_carrier_priority_allowed() is False
    pause_state[0] = {"status": "query_error", "entries_paused": True}
    assert main._paused_forecast_carrier_priority_allowed() is False

    def unreadable_pause_state():
        raise OSError("control-plane read failed")

    monkeypatch.setattr(
        control_plane,
        "_refresh_entries_pause_from_durable_state",
        unreadable_pause_state,
    )
    assert main._paused_forecast_carrier_priority_allowed() is False


def test_paused_forecast_yield_requires_successful_day0_monitor():
    import src.main as main
    from src.runtime.reactor_wake import ReactorWake

    wake = ReactorWake(
        "day0-monitor",
        "2026-08-02T12:00:00+00:00",
        "day0",
        "day0_extreme_event_committed",
    )
    main._edli_initialize_reactor_wake_cursor()
    try:
        main._yield_incomplete_day0_after_monitor_once(
            wake,
            monitor_succeeded=False,
        )
        assert main._edli_paused_forecast_post_monitor_yield.wake_ids == frozenset()

        # A generic queue yield can mean another monitor is merely in flight;
        # it is not proof that exposure priority completed.
        main._edli_day0_post_monitor_yield.arm(wake.wake_id)
        assert main._edli_paused_forecast_post_monitor_yield.wake_ids == frozenset()

        main._yield_incomplete_day0_after_monitor_once(
            wake,
            monitor_succeeded=True,
        )
        assert main._edli_paused_forecast_post_monitor_yield.wake_ids == {
            wake.wake_id
        }
    finally:
        main._edli_initialize_reactor_wake_cursor()


def test_paused_empty_exposure_selects_forecast_carrier_without_day0_yield(
    monkeypatch,
):
    """An empty canonical monitor set permits one paused no-submit carrier turn."""

    import src.control.control_plane as control_plane
    import src.main as main
    from src.runtime import reactor_wake

    family = ("Paris", "2026-08-03", "high")
    forecast = reactor_wake.ReactorWake(
        "forecast-carrier",
        "2026-08-02T12:00:00+00:00",
        "forecast",
        "forecast_posterior_advanced",
        forecast_families=(family,),
    )
    day0 = reactor_wake.ReactorWake(
        "day0-entry",
        "2026-08-02T12:00:01+00:00",
        "day0",
        "day0_extreme_event_committed",
        forecast_families=(family,),
    )
    selections: list[dict[str, object]] = []
    cycle_kwargs: dict[str, object] = {}

    monkeypatch.setattr(
        control_plane,
        "_refresh_entries_pause_from_durable_state",
        lambda: {"status": "ok", "entries_paused": True},
    )
    monkeypatch.setattr(main, "_current_periodic_monitor_obligation_count", lambda: 0)
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_collateral_authority_wake_backoff_ids",
        lambda: frozenset(),
    )
    monkeypatch.setattr(main, "_forecast_wake_held_families", lambda _families: frozenset())
    monkeypatch.setattr(main, "_edli_reactor_active_lock", threading.Lock())
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: cycle_kwargs.update(kwargs) is None,
    )
    monkeypatch.setattr(
        main,
        "_acknowledge_edli_reactor_wake_batch",
        lambda *_args, **_kwargs: True,
    )

    def read_wake(**kwargs):
        selections.append(kwargs)
        return forecast if kwargs["prefer_forecast_carrier_progress"] else day0

    monkeypatch.setattr(reactor_wake, "read_reactor_wake", read_wake)
    monkeypatch.setattr(
        reactor_wake,
        "coalescible_reactor_wakes",
        lambda selected: (selected,),
    )
    main._edli_initialize_reactor_wake_cursor()
    try:
        assert main._edli_day0_post_monitor_yield.wake_ids == frozenset()
        assert main._edli_reactor_wake_poll_once() is True
        assert selections == [
            {
                "prefer_forecast_carrier_progress": True,
                "fail_on_error": True,
            }
        ]
        assert cycle_kwargs["producer_wake_ids"] == (forecast.wake_id,)
        assert cycle_kwargs["allow_paused_forecast_snapshot_completion"] is True
    finally:
        main._edli_initialize_reactor_wake_cursor()


def test_paused_open_exposure_selects_forecast_after_day0_monitor_yield(
    monkeypatch,
):
    """A completed Day0 monitor earns one bounded no-submit carrier turn."""

    import src.control.control_plane as control_plane
    import src.main as main
    from src.runtime import reactor_wake

    family = ("Paris", "2026-08-03", "high")
    forecast = reactor_wake.ReactorWake(
        "forecast-carrier",
        "2026-08-02T12:00:00+00:00",
        "forecast",
        "forecast_posterior_advanced",
        forecast_families=(family,),
    )
    day0 = reactor_wake.ReactorWake(
        "day0-monitored",
        "2026-08-02T12:00:01+00:00",
        "day0",
        "day0_extreme_event_committed",
        forecast_families=(family,),
    )
    selections: list[dict[str, object]] = []
    cycle_kwargs: dict[str, object] = {}

    monkeypatch.setattr(
        control_plane,
        "_refresh_entries_pause_from_durable_state",
        lambda: {"status": "ok", "entries_paused": True},
    )
    monkeypatch.setattr(main, "_current_periodic_monitor_obligation_count", lambda: 1)
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_collateral_authority_wake_backoff_ids",
        lambda: frozenset(),
    )
    monkeypatch.setattr(main, "_forecast_wake_held_families", lambda _families: frozenset())
    monkeypatch.setattr(main, "_edli_reactor_active_lock", threading.Lock())
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: cycle_kwargs.update(kwargs) is None,
    )
    monkeypatch.setattr(
        main,
        "_acknowledge_edli_reactor_wake_batch",
        lambda *_args, **_kwargs: True,
    )

    def read_wake(**kwargs):
        selections.append(kwargs)
        return forecast

    monkeypatch.setattr(reactor_wake, "read_reactor_wake", read_wake)
    monkeypatch.setattr(
        reactor_wake,
        "coalescible_reactor_wakes",
        lambda selected: (selected,),
    )
    main._edli_initialize_reactor_wake_cursor()
    try:
        main._yield_incomplete_day0_after_monitor_once(
            day0,
            monitor_succeeded=True,
        )
        assert main._edli_reactor_wake_poll_once() is True
        assert selections == [
            {
                "exclude_wake_ids": frozenset({day0.wake_id}),
                "prefer_exact_held_sell": True,
                "prefer_forecast_carrier_progress": True,
                "fail_on_error": True,
            }
        ]
        assert cycle_kwargs["producer_wake_ids"] == (forecast.wake_id,)
        assert cycle_kwargs["allow_paused_forecast_snapshot_completion"] is True
    finally:
        main._edli_initialize_reactor_wake_cursor()


def test_active_foreign_monitor_yields_day0_for_one_material_wake(monkeypatch):
    import src.main as main
    from src.runtime import reactor_wake

    family = ("Paris", "2026-08-12", "high")
    day0 = reactor_wake.ReactorWake(
        "day0-held",
        "2026-08-11T23:00:00+00:00",
        "day0",
        "day0_extreme_event_committed",
        forecast_families=(family,),
    )
    forecast = reactor_wake.ReactorWake(
        "forecast-independent",
        "2026-08-11T23:00:01+00:00",
        "forecast",
        "forecast_posterior_advanced",
        forecast_families=(family,),
    )
    monitor_active = threading.Event()
    monitor_active.set()
    selections: list[dict[str, object]] = []
    cycle_wake_ids: list[tuple[str, ...]] = []
    acknowledged: list[str] = []

    monkeypatch.setattr(main, "_held_position_monitor_active", monitor_active)
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_collateral_authority_wake_backoff_ids",
        lambda: frozenset(),
    )
    monkeypatch.setattr(
        main,
        "_paused_forecast_carrier_priority_allowed",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        main,
        "_forecast_wake_held_families",
        lambda _families: frozenset(),
    )
    monkeypatch.setattr(main, "_edli_reactor_active_lock", threading.Lock())
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: cycle_wake_ids.append(kwargs["producer_wake_ids"]) or True,
    )
    monkeypatch.setattr(
        main,
        "_acknowledge_edli_reactor_wake_batch",
        lambda wake, *_args, **_kwargs: acknowledged.append(wake.wake_id) or True,
    )
    monkeypatch.setattr(
        reactor_wake,
        "exact_held_sell_completion_wake_ids",
        lambda **_kwargs: frozenset(),
    )

    def read_wake(**kwargs):
        selections.append(kwargs)
        excluded = frozenset(kwargs.get("exclude_wake_ids", ()))
        return forecast if day0.wake_id in excluded else day0

    monkeypatch.setattr(reactor_wake, "read_reactor_wake", read_wake)
    monkeypatch.setattr(
        reactor_wake,
        "coalescible_reactor_wakes",
        lambda selected: (selected,),
    )
    main._edli_initialize_reactor_wake_cursor()
    try:
        assert main._edli_reactor_wake_poll_once() is False
        assert main._edli_day0_post_monitor_yield.wake_ids == {day0.wake_id}
        assert acknowledged == []

        assert main._edli_reactor_wake_poll_once() is True
        assert selections[1]["exclude_wake_ids"] == frozenset({day0.wake_id})
        assert cycle_wake_ids == [(forecast.wake_id,)]
        assert acknowledged == [forecast.wake_id]
    finally:
        main._day0_urgent_wake_pending.clear()
        main._edli_initialize_reactor_wake_cursor()


@pytest.mark.parametrize("exposure", (1, None), ids=("nonempty", "unknown"))
@pytest.mark.parametrize(
    "pause_state",
    (
        {"status": "ok", "entries_paused": True},
        {"status": "ok", "entries_paused": False},
        {"status": "query_error", "entries_paused": True},
    ),
    ids=("paused", "pause-clear", "pause-unreadable"),
)
def test_wake_poll_retains_day0_priority_without_paused_empty_exposure_proof(
    monkeypatch,
    exposure,
    pause_state,
):
    import src.control.control_plane as control_plane
    import src.main as main
    from src.runtime import reactor_wake

    family = ("Paris", "2026-08-03", "high")
    forecast = reactor_wake.ReactorWake(
        "forecast-carrier",
        "2026-08-02T12:00:00+00:00",
        "forecast",
        "forecast_posterior_advanced",
        forecast_families=(family,),
    )
    day0 = reactor_wake.ReactorWake(
        "day0-entry",
        "2026-08-02T12:00:01+00:00",
        "day0",
        "day0_extreme_event_committed",
        forecast_families=(family,),
    )
    selections: list[dict[str, object]] = []
    cycle_kwargs: dict[str, object] = {}

    monkeypatch.setattr(
        control_plane,
        "_refresh_entries_pause_from_durable_state",
        lambda: pause_state,
    )
    monkeypatch.setattr(
        main,
        "_current_periodic_monitor_obligation_count",
        lambda: exposure,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_collateral_authority_wake_backoff_ids",
        lambda: frozenset(),
    )
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _families: False)
    monkeypatch.setattr(main, "_pending_held_day0_wake_families", lambda: frozenset())
    monkeypatch.setattr(main, "_record_day0_no_monitor_completion", lambda _wake_id: True)
    monkeypatch.setattr(main, "_edli_reactor_active_lock", threading.Lock())
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: cycle_kwargs.update(kwargs) is None,
    )
    monkeypatch.setattr(
        main,
        "_acknowledge_edli_reactor_wake_batch",
        lambda *_args, **_kwargs: True,
    )

    def read_wake(**kwargs):
        selections.append(kwargs)
        return forecast if kwargs["prefer_forecast_carrier_progress"] else day0

    monkeypatch.setattr(reactor_wake, "read_reactor_wake", read_wake)
    monkeypatch.setattr(
        reactor_wake,
        "coalescible_reactor_wakes",
        lambda selected: (selected,),
    )
    main._edli_initialize_reactor_wake_cursor()
    try:
        if not (
            pause_state == {"status": "ok", "entries_paused": True}
            and exposure == 1
        ):
            # A previously successful monitor certificate cannot alter wake
            # priority after pause authority clears/degrades or exposure turns
            # unknown.
            main._edli_paused_forecast_post_monitor_yield.arm(day0.wake_id)
        assert main._edli_reactor_wake_poll_once() is True
        assert selections == [
            {
                "prefer_forecast_carrier_progress": False,
                "fail_on_error": False,
            }
        ]
        assert cycle_kwargs["producer_wake_ids"] == (day0.wake_id,)
        assert cycle_kwargs["allow_paused_forecast_snapshot_completion"] is False
    finally:
        main._day0_urgent_wake_pending.clear()
        main._edli_initialize_reactor_wake_cursor()


def test_exact_held_sell_debt_probe_rejects_malformed_queue_file(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    queue_dir = reactor_wake._wake_queue_dir(path)
    queue_dir.mkdir(parents=True)
    (queue_dir / "malformed.json").write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="REACTOR_WAKE_INVALID"):
        reactor_wake.exact_held_sell_completion_wake_ids(
            path=path,
            fail_on_error=True,
        )


def test_strict_wake_selection_rejects_unreadable_queue_dir(monkeypatch, tmp_path):
    from src.runtime import reactor_wake

    class UnreadableQueueDir:
        def stat(self):
            raise PermissionError("queue directory unreadable")

    path = tmp_path / "wake.json"
    monkeypatch.setattr(
        reactor_wake,
        "_wake_queue_dir",
        lambda _path: UnreadableQueueDir(),
    )

    assert reactor_wake._queued_wakes(path) == []
    with pytest.raises(PermissionError, match="queue directory unreadable"):
        reactor_wake.read_reactor_wake(path=path, fail_on_error=True)


def test_strict_wake_selection_validates_legacy_before_queued_forecast(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    forecast = reactor_wake.publish_reactor_wake(
        source="forecast",
        reason="forecast_posterior_advanced",
        path=path,
        wake_id="queued-forecast",
        published_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        forecast_families=(("Paris", "2026-08-03", "high"),),
    )
    assert reactor_wake._wake_queue_target(forecast, path=path).exists()
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="REACTOR_WAKE_INVALID"):
        reactor_wake.read_reactor_wake(path=path, fail_on_error=True)


def test_strict_wake_selection_rejects_regular_file_queue_path(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    reactor_wake._wake_queue_dir(path).write_text("not-a-directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="not a directory"):
        reactor_wake.read_reactor_wake(path=path, fail_on_error=True)


def test_paused_forecast_selection_scans_large_queue_once(tmp_path, monkeypatch):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    queue_dir = reactor_wake._wake_queue_dir(path)
    queue_dir.mkdir(parents=True)
    for index in range(256):
        wake = reactor_wake.ReactorWake(
            wake_id=f"wake-{index:04d}",
            published_at=(
                datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
                + timedelta(seconds=index)
            ).isoformat(),
            source="test",
            reason="market_price_advanced",
        )
        reactor_wake._atomic_write_wake(
            queue_dir / f"{index:04d}-{wake.wake_id}.json",
            wake,
        )
    forecast = reactor_wake.ReactorWake(
        wake_id="forecast-latest",
        published_at="2026-08-02T12:05:00+00:00",
        source="test",
        reason="forecast_posterior_advanced",
        forecast_families=(("Paris", "2026-08-03", "high"),),
    )
    reactor_wake._atomic_write_wake(queue_dir / "9999-forecast-latest.json", forecast)

    original = reactor_wake._queued_wakes
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(reactor_wake, "_queued_wakes", counted)

    assert (
        reactor_wake.read_reactor_wake(
            path=path,
            prefer_forecast_carrier_progress=True,
            fail_on_error=True,
        )
        == forecast
    )
    assert calls == 1


def test_exact_held_sell_debt_preempts_older_generic_completion_marker(tmp_path):
    """A failed broad fairness cut cannot head-of-line block exact held capital."""
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="periodic_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="generic-completion-old",
        published_at=datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc),
        event_ids=tuple(f"event-{index}" for index in range(100)),
        forecast_families=tuple(
            (f"City-{index}", "2026-07-30", "high") for index in range(100)
        ),
    )
    request = reactor_wake.make_held_sell_reauction_request(
        position_id="position-capital-at-risk",
        family=("Paris", "2026-07-30", "low"),
        probability_content_identity="q-current",
        held_token_id="token-held",
        held_best_bid=0.11,
        bid_observed_at="2026-07-30T08:00:01+00:00",
    )
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="exact-held-sell-new",
        published_at=datetime(2026, 7, 30, 8, 0, 1, tzinfo=timezone.utc),
        forecast_families=(request.family,),
        held_sell_reauction_requests=(request,),
    )

    selected = reactor_wake.read_reactor_wake(path=path)

    assert selected is not None
    assert selected.wake_id == "exact-held-sell-new"
    assert selected.held_sell_reauction_requests == (request,)


def test_exact_held_sell_batch_reserves_one_generic_completion_turn(tmp_path):
    """Full-scope generic gets a bounded turn under continuous exact debt."""
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="periodic_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="generic-completion-old",
        published_at=datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc),
        event_ids=tuple(f"event-{index}" for index in range(100)),
        forecast_families=tuple(
            (f"City-{index}", "2026-07-30", "high") for index in range(100)
        ),
    )
    for index in range(40):
        request = reactor_wake.make_held_sell_reauction_request(
            position_id=f"position-{index}",
            family=("Paris", "2026-07-30", "low"),
            probability_content_identity=f"q-{index}",
            held_token_id=f"token-{index}",
            held_best_bid=0.11,
            bid_observed_at="2026-07-30T08:00:01+00:00",
        )
        reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=path,
            wake_id=f"exact-held-sell-{index:02d}",
            published_at=datetime(
                2026, 7, 30, 8, 0, 1, index, tzinfo=timezone.utc
            ),
            forecast_families=(request.family,),
            held_sell_reauction_requests=(request,),
        )

    selected = reactor_wake.read_reactor_wake(path=path)
    assert selected is not None
    assert selected.wake_id == "exact-held-sell-00"

    batch = reactor_wake.coalescible_reactor_wakes(selected, path=path)
    assert batch[0].wake_id == "exact-held-sell-00"
    assert batch[1].wake_id == "generic-completion-old"
    assert len(batch) == reactor_wake.GLOBAL_AUCTION_COMPLETION_COALESCE_LIMIT


def test_held_sell_completion_drain_is_bounded_and_position_fair(tmp_path):
    """Historical held-SELL anchors get one completion turn before duplicates."""
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    anchors = (
        "39a8fb42-e3a:monitor_refreshed:161",
        "b0ddf606-f63:monitor_refreshed:269",
        "d8b9e6b5-bc2:monitor_refreshed:433",
        "e582b997-daf:monitor_refreshed:191",
        "c25321a7-f17:monitor_refreshed:337",
    )
    position_ids = (*anchors, anchors[0], *(f"position-{index}" for index in range(20)))
    for index, position_id in enumerate(position_ids):
        request = reactor_wake.make_held_sell_reauction_request(
            position_id=position_id,
            family=("Paris", "2026-07-30", "low"),
            probability_content_identity=f"q-{index}",
            held_token_id=f"token-{index}",
            held_best_bid=0.11,
            bid_observed_at="2026-07-30T08:00:00+00:00",
        )
        reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=path,
            wake_id=f"completion-{index:02d}",
            published_at=datetime(2026, 7, 30, 8, 0, index, tzinfo=timezone.utc),
            forecast_families=(request.family,),
            held_sell_reauction_requests=(request,),
        )

    selected = reactor_wake.read_reactor_wake(path=path)
    assert selected is not None
    batch = reactor_wake.coalescible_reactor_wakes(selected, path=path)
    drained_positions = tuple(
        request.position_id
        for wake in batch
        for request in wake.held_sell_reauction_requests
    )

    assert len(batch) == reactor_wake.GLOBAL_AUCTION_COMPLETION_COALESCE_LIMIT
    assert len(drained_positions) == len(set(drained_positions))
    assert set(anchors).issubset(drained_positions)


def test_position_fill_wake_is_an_exact_targeted_reactor_fast_path():
    from src.events.reactor import run_edli_event_reactor_cycle

    source = inspect.getsource(run_edli_event_reactor_cycle)

    assert 'producer_wake_reason == "position_fill_projected"' in source
    assert "committed_position_fill_wake" in source
    assert "or committed_position_fill_wake" in source
    assert "targeted_only=producer_fast_path" in source
    assert "forecast_posterior_wake or bool(targeted_event_ids)" in source


def test_targeted_forecast_wake_ignores_only_older_remaining_backlog(monkeypatch):
    from src.events.reactor import _reactor_wake_cancellation_probe
    from src.runtime import reactor_wake

    older = reactor_wake.ReactorWake(
        "wake-older",
        "2026-07-19T11:59:59+00:00",
        "forecast",
        "forecast_posterior_advanced",
        forecast_families=(("Paris", "2026-07-20", "high"),),
    )
    revisions = iter(("base", "new"))
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_revision",
        lambda: next(revisions),
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda _published_at, *, exclude_wake_ids=(): (),
    )

    cancelled = _reactor_wake_cancellation_probe(
        producer_wake_reason="forecast_posterior_advanced",
        producer_wake_ids=("wake-current",),
        producer_wake_published_at="2026-07-19T12:00:00+00:00",
        forecast_wake_families={("Paris", "2026-07-20", "high")},
        urgent_day0_pending=None,
    )

    assert cancelled() is False


def test_paused_targeted_forecast_wake_keeps_overlapping_new_wake_queued(monkeypatch):
    """The selected no-submit carrier survives a same-family producer wake."""

    from src.events.reactor import _reactor_wake_cancellation_probe
    from src.runtime import reactor_wake

    pending = reactor_wake.ReactorWake(
        "wake-new-overlap",
        "2026-07-19T12:00:01+00:00",
        "forecast",
        "forecast_posterior_advanced",
        forecast_families=(("Paris", "2026-07-20", "high"),),
    )
    pending_wakes = [pending]
    revisions = iter(("base", "new", "new"))
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_revision",
        lambda: next(revisions),
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda _published_at, *, exclude_wake_ids=(): tuple(pending_wakes),
    )

    cancelled = _reactor_wake_cancellation_probe(
        producer_wake_reason="forecast_posterior_advanced",
        producer_wake_ids=("wake-current",),
        producer_wake_published_at="2026-07-19T12:00:00+00:00",
        forecast_wake_families={("Paris", "2026-07-20", "high")},
        urgent_day0_pending=None,
        allow_paused_forecast_snapshot_completion=True,
    )

    assert cancelled() is False
    assert cancelled() is False
    assert pending_wakes == [pending]


@pytest.mark.parametrize(
    "producer_reason,producer_families",
    [
        ("market_price_advanced", set()),
        (
            "forecast_posterior_advanced",
            {("Paris", "2026-07-20", "high")},
        ),
    ],
)
@pytest.mark.parametrize(
    "pending_reason",
    ["market_price_advanced", "money_path_substrate_refreshed"],
)
def test_reactor_wake_probe_ignores_book_only_revision(
    monkeypatch,
    producer_reason,
    producer_families,
    pending_reason,
):
    from src.events.reactor import _reactor_wake_cancellation_probe
    from src.runtime import reactor_wake

    pending = reactor_wake.ReactorWake(
        "wake-substrate",
        "2026-07-19T12:00:01+00:00",
        "substrate_observer",
        pending_reason,
        forecast_families=(("Paris", "2026-07-20", "high"),),
    )
    revisions = iter(("base", "new", "new"))
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_revision",
        lambda: next(revisions),
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda _published_at, *, exclude_wake_ids=(): (pending,),
    )

    cancelled = _reactor_wake_cancellation_probe(
        producer_wake_reason=producer_reason,
        producer_wake_ids=("wake-current",),
        producer_wake_published_at="2026-07-19T12:00:00+00:00",
        forecast_wake_families=producer_families,
        urgent_day0_pending=None,
    )

    assert cancelled() is False
    assert cancelled() is False


@pytest.mark.parametrize(
    "pending_reason,pending_families",
    [
        (
            "forecast_posterior_advanced",
            (("Paris", "2026-07-20", "high"),),
        ),
        ("day0_extreme_event_committed", ()),
    ],
)
def test_targeted_forecast_wake_stops_for_dependent_or_faster_revision(
    monkeypatch,
    pending_reason,
    pending_families,
):
    from src.events.reactor import _reactor_wake_cancellation_probe
    from src.runtime import reactor_wake

    pending = reactor_wake.ReactorWake(
        "wake-pending",
        "2026-07-19T12:00:01+00:00",
        "producer",
        pending_reason,
        forecast_families=pending_families,
    )
    revisions = iter(("base", "new"))
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_revision",
        lambda: next(revisions),
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda _published_at, *, exclude_wake_ids=(): (pending,),
    )

    cancelled = _reactor_wake_cancellation_probe(
        producer_wake_reason="forecast_posterior_advanced",
        producer_wake_ids=("wake-current",),
        producer_wake_published_at="2026-07-19T12:00:00+00:00",
        forecast_wake_families={("Paris", "2026-07-20", "high")},
        urgent_day0_pending=None,
    )

    assert cancelled() is True


@pytest.mark.parametrize(
    "pending_reason,pending_event_ids,pending_held_sell_requests",
    [
        ("day0_extreme_event_committed", (), ()),
        ("position_fill_projected", ("fill-event",), ()),
        ("market_price_advanced", ("price-event",), ()),
        ("money_path_substrate_refreshed", (), ()),
        (
            "held_sell_global_auction_completion_requested",
            (),
            (object(),),
        ),
        ("unknown_producer_reason", (), ()),
    ],
)
def test_paused_targeted_forecast_wake_still_cancels_capital_risk_invalidators(
    monkeypatch,
    pending_reason,
    pending_event_ids,
    pending_held_sell_requests,
):
    from src.events.reactor import _reactor_wake_cancellation_probe
    from src.runtime import reactor_wake

    pending = reactor_wake.ReactorWake(
        "wake-invalidator",
        "2026-07-19T12:00:01+00:00",
        "producer",
        pending_reason,
        event_ids=pending_event_ids,
        held_sell_reauction_requests=pending_held_sell_requests,
    )
    revisions = iter(("base", "new"))
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_revision",
        lambda: next(revisions),
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda _published_at, *, exclude_wake_ids=(): (pending,),
    )

    cancelled = _reactor_wake_cancellation_probe(
        producer_wake_reason="forecast_posterior_advanced",
        producer_wake_ids=("wake-current",),
        producer_wake_published_at="2026-07-19T12:00:00+00:00",
        forecast_wake_families={("Paris", "2026-07-20", "high")},
        urgent_day0_pending=None,
        allow_paused_forecast_snapshot_completion=True,
    )

    assert cancelled() is True


def test_targeted_forecast_supersession_aborts_cycle_before_ack_boundary():
    from src.events.reactor import run_edli_event_reactor_cycle

    source = inspect.getsource(run_edli_event_reactor_cycle)
    build = source.index("_edli_build_forecast_snapshot_events(")
    superseded = source.index(
        "if targeted_forecast_wake and _urgent_wake_pending():",
        build,
    )
    process_pending = source.index("reactor.process_pending(", superseded)

    assert build < superseded < process_pending


def _global_batch_probe_reactor(
    store,
    observations,
    *,
    incomplete=False,
    next_claim_event=None,
    held_sell_completion_cut=None,
):
    bound_claims = {"generations": {}, "attempt_counts": {}}

    def _direct_submit(*_args, **_kwargs):
        observations["direct_submit_calls"] += 1
        raise AssertionError("global batch path must not invoke per-event submit")

    def _bind_global_claim_generations(generations, attempt_counts):
        bound_claims["generations"] = generations or {}
        bound_claims["attempt_counts"] = attempt_counts or {}
        observations.setdefault("claim_bind_calls", []).append(
            (generations is not None, attempt_counts is not None)
        )

    def _process_global_batch(
        events,
        _decision_time,
        *,
        claim_unpaged_winner=None,
    ):
        observations["batch_calls"] += 1
        observations["batch_event_ids"] = tuple(event.event_id for event in events)
        observations["mutex_locked_at_batch"] = world_write_mutex().locked()
        observations["world_conn_in_txn_at_batch"] = bool(store.conn.in_transaction)
        observations["claimed_statuses_at_batch"] = tuple(
            _processing_status(store.conn, event.event_id) for event in events
        )
        observations["claim_generations_at_batch"] = dict(
            bound_claims["generations"]
        )
        observations["claim_attempt_counts_at_batch"] = dict(
            bound_claims["attempt_counts"]
        )
        receipts = {
            event.event_id: EventSubmissionReceipt(
                submitted=False,
                event_id=event.event_id,
                causal_snapshot_id=event.causal_snapshot_id,
                reason="SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_NO_CURRENT_WINNER",
                proof_accepted=False,
            )
            for event in events
        }
        if incomplete:
            receipts.pop(events[-1].event_id)
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=None,
            venue_submit_count=0,
            next_claim_event=next_claim_event,
            held_sell_completion_cut=held_sell_completion_cut,
        )

    observations.update(direct_submit_calls=0, batch_calls=0)
    _direct_submit.process_global_batch = _process_global_batch  # type: ignore[attr-defined]
    _direct_submit.bind_global_claim_generations = (  # type: ignore[attr-defined]
        _bind_global_claim_generations
    )
    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_direct_submit,
        reject=lambda *_args: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )


def test_reactor_carries_immutable_held_sell_cut_from_global_batch_result():
    _conn, store = _store()
    event = _forecast_event("held-cut-transfer", target_date="2026-05-25")
    store.insert_or_ignore(event)
    cut = _held_sell_completion_result(
        position_id="held-cut-transfer",
        token_id="token-held-cut-transfer",
        probability_content_identity="q-held-cut-transfer",
        outcome="INCOMPLETE",
    ).global_held_sell_completion_cuts[0]
    reactor = _global_batch_probe_reactor(
        store,
        {},
        held_sell_completion_cut=cut,
    )

    result = reactor.process_pending(
        decision_time=_DT_VENUE_OPEN,
        limit=1,
    )

    assert result.global_held_sell_completion_cuts == [cut]


def _terminal_surfaces(conn: sqlite3.Connection, event_id: str) -> dict[str, int]:
    verified_no_submit = conn.execute(
        """
        SELECT COUNT(*)
        FROM edli_no_submit_receipts AS receipt
        JOIN decision_certificates AS cert
          ON cert.certificate_type = 'PreSubmitDecisionCertificate'
         AND cert.verifier_status = 'VERIFIED'
         AND json_extract(cert.payload_json, '$.event_id') = receipt.event_id
         AND json_extract(cert.payload_json, '$.projection_hash') = receipt.projection_hash
        WHERE receipt.event_id = ?
        """,
        (event_id,),
    ).fetchone()[0]
    compile_failure = conn.execute(
        "SELECT COUNT(*) FROM decision_compile_failures WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]
    regret = conn.execute(
        "SELECT COUNT(*) FROM no_trade_regret_events WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]
    dead_letter = conn.execute(
        "SELECT COUNT(*) FROM event_dead_letters WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]
    execution_receipt = conn.execute(
        """
        SELECT COUNT(*)
        FROM decision_certificates
        WHERE certificate_type = 'ExecutionReceiptCertificate'
          AND verifier_status = 'VERIFIED'
          AND json_extract(payload_json, '$.event_id') = ?
        """,
        (event_id,),
    ).fetchone()[0]
    return {
        "verified_no_submit": verified_no_submit,
        "execution_receipt": execution_receipt,
        "compile_failure": compile_failure,
        "regret": regret,
        "dead_letter": dead_letter,
    }


def test_event_cannot_bypass_source_truth():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    reactor, rejected, submitted = _reactor(store, gates=False)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.rejected == 1
    assert rejected[0][1] == "SOURCE_TRUTH"
    assert submitted == []


def test_market_channel_event_not_direct_reactor_input():
    _conn, store = _store()
    event = _market_event()
    store.insert_or_ignore(event)
    reactor, rejected, submitted = _reactor(store)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.rejection_reasons == []
    assert result.processed == 0
    assert result.rejected == 0
    assert rejected == []
    assert submitted == []
    assert _processing_status(_conn, event.event_id) is None


def test_global_batch_claims_epoch_then_calls_one_lock_free_batch_seam():
    conn, store = _store()
    events = (
        _forecast_event("global-a", target_date="2026-05-25"),
        _forecast_event("global-b", target_date="2026-05-25"),
    )
    for event in events:
        store.insert_or_ignore(event)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=2)

    assert observations["batch_calls"] == 1
    assert observations["direct_submit_calls"] == 0
    assert set(observations["batch_event_ids"]) == {event.event_id for event in events}
    assert observations["mutex_locked_at_batch"] is False
    assert observations["world_conn_in_txn_at_batch"] is False
    assert observations["claimed_statuses_at_batch"] == ("processing", "processing")
    assert observations["claim_generations_at_batch"] == {
        event.event_id: _DT_VENUE_OPEN.isoformat() for event in events
    }
    assert observations["claim_attempt_counts_at_batch"] == {
        event.event_id: 1 for event in events
    }
    assert observations["claim_bind_calls"] == [(True, True), (False, False)]
    assert result.retried == 2
    assert all(_processing_status(conn, event.event_id) == "pending" for event in events)
    assert {
        row[0]
        for row in conn.execute(
            "SELECT last_error FROM opportunity_event_processing "
            "WHERE event_id IN (?, ?)",
            tuple(event.event_id for event in events),
        )
    } == {"SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_NO_CURRENT_WINNER"}


def test_global_batch_stops_claiming_when_cycle_is_cancelled():
    conn, store = _store()
    events = (
        _forecast_event("cancel-global-a", target_date="2026-05-25"),
        _forecast_event("cancel-global-b", target_date="2026-05-25"),
    )
    for event in events:
        store.insert_or_ignore(event)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    reactor.process_pending(
        decision_time=_DT_VENUE_OPEN,
        limit=2,
        cancelled=lambda: any(
            _processing_status(conn, event.event_id) == "processing"
            for event in events
        ),
    )

    assert len(observations["batch_event_ids"]) == 1
    assert sum(
        _processing_status(conn, event.event_id) == "pending"
        for event in events
    ) == 2


def test_process_pending_cancellation_includes_monitor_debt_except_exact_completion():
    def any_urgent():
        return False

    def day0_urgent():
        return True

    assert _process_pending_cancelled(
        committed_day0_wake=True,
        producer_fast_path=True,
        urgent_wake_pending=any_urgent,
        urgent_day0_pending=day0_urgent,
    ) is None
    assert _process_pending_cancelled(
        committed_day0_wake=False,
        producer_fast_path=True,
        urgent_wake_pending=any_urgent,
        urgent_day0_pending=day0_urgent,
    ) is day0_urgent
    assert _process_pending_cancelled(
        committed_day0_wake=False,
        producer_fast_path=False,
        urgent_wake_pending=any_urgent,
        urgent_day0_pending=day0_urgent,
    ) is any_urgent

    debt_pending = [False]
    cancelled = _process_pending_cancelled(
        committed_day0_wake=False,
        producer_fast_path=False,
        urgent_wake_pending=any_urgent,
        urgent_day0_pending=day0_urgent,
        held_position_monitor_debt_pending=lambda: debt_pending[0],
    )
    assert cancelled is not None
    assert cancelled() is False
    debt_pending[0] = True
    assert cancelled() is True
    assert _process_pending_cancelled(
        committed_day0_wake=False,
        producer_fast_path=False,
        urgent_wake_pending=any_urgent,
        urgent_day0_pending=day0_urgent,
        held_position_monitor_debt_pending=lambda: True,
        exact_held_completion=True,
    ) is any_urgent
    assert _held_position_monitor_preemption_pending(
        lambda: False,
        lambda: True,
    ) is True
    assert _process_pending_cancelled(
        committed_day0_wake=True,
        producer_fast_path=True,
        urgent_wake_pending=any_urgent,
        urgent_day0_pending=day0_urgent,
        held_position_monitor_debt_pending=lambda: True,
    ) is None


def test_monitor_debt_preempts_global_batch_and_leaves_queue_retryable():
    conn, store = _store()
    events = _multiwinner_events("monitor-debt", 3)
    for event in events:
        store.insert_or_ignore(event)
    debt_pending = [False]

    def _batch(claimed, decision_time, *, claim_unpaged_winner=None):
        outcome = _sequential_winner_batch(
            claimed,
            decision_time,
            claim_unpaged_winner=claim_unpaged_winner,
        )
        debt_pending[0] = True
        return outcome

    reactor = _multiwinner_reactor(store, _batch)
    cancelled = _process_pending_cancelled(
        committed_day0_wake=False,
        producer_fast_path=False,
        urgent_wake_pending=lambda: False,
        urgent_day0_pending=None,
        held_position_monitor_debt_pending=lambda: debt_pending[0],
    )
    reactor.process_pending(
        decision_time=_DT_VENUE_OPEN,
        limit=None,
        cancelled=cancelled,
    )

    statuses = {
        event.event_id: _processing_status(conn, event.event_id) for event in events
    }
    assert sorted(statuses.values()) == ["pending", "pending", "processing"]


def test_monitor_debt_yields_before_runtime_setup_and_releases_reactor_lock(
    monkeypatch,
):
    import src.events.reactor as reactor_module
    import src.main as main
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    import src.state.db as db

    monkeypatch.setattr(main, "_settings_section", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(
        reactor_module,
        "_durable_exact_held_sell_completion_pending",
        lambda: False,
    )
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda: pytest.fail("monitor debt must yield before runtime DB setup"),
    )
    reservations: list[tuple[str, str]] = []

    def reserve_completion(**kwargs):
        reservations.append((kwargs["reason"], kwargs["position_id"]))
        reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
        return True

    monkeypatch.setattr(
        reactor_module,
        "request_global_auction_completion",
        reserve_completion,
    )

    lock = threading.Lock()
    reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        assert reactor_module.run_edli_event_reactor_cycle(
            active_lock=lock,
            held_position_monitor_debt_pending=lambda: True,
        ) is False
        assert reservations == [("periodic_monitor_preemption", "")]
        assert reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set()
        assert not lock.locked()
    finally:
        reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_reserved_monitor_completion_reaches_runtime_setup_under_monitor_pressure(
    monkeypatch,
):
    import src.events.reactor as reactor_module
    import src.main as main
    import src.state.db as db
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    class RuntimeSetupReached(RuntimeError):
        pass

    monkeypatch.setattr(main, "_settings_section", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(
        reactor_module,
        "_durable_exact_held_sell_completion_pending",
        lambda: False,
    )
    monkeypatch.setattr(
        reactor_module,
        "_paused_entry_wake_should_park",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda: (_ for _ in ()).throw(RuntimeSetupReached()),
    )

    reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
    try:
        with pytest.raises(RuntimeSetupReached):
            reactor_module.run_edli_event_reactor_cycle(
                active_lock=threading.Lock(),
                held_position_monitor_debt_pending=lambda: True,
            )
    finally:
        reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


@pytest.mark.parametrize("preemption", (False, True), ids=("deadline", "monitor"))
def test_reactor_construct_slow_sql_is_bounded_and_releases_resources(
    monkeypatch,
    tmp_path,
    preemption,
):
    import src.engine.event_reactor_adapter as adapter_module
    import src.events.reactor as reactor_module
    import src.main as main
    import src.runtime.bankroll_provider as bankroll_provider
    import src.state.db as db
    import src.state.portfolio as portfolio_module
    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel
    from src.runtime import reactor_wake

    world_path = tmp_path / "world.db"
    forecasts_path = tmp_path / "forecasts.db"
    trade_path = tmp_path / "trades.db"
    world = sqlite3.connect(world_path)
    init_schema(world)
    world.close()
    sqlite3.connect(forecasts_path).close()
    sqlite3.connect(trade_path).close()

    opened: list[sqlite3.Connection] = []
    trade_connection_kwargs: list[dict[str, object]] = []
    query_entered = threading.Event()
    monitor_pressure = threading.Event()

    def world_connection():
        conn = sqlite3.connect(world_path)
        conn.row_factory = sqlite3.Row
        opened.append(conn)
        return conn

    def forecasts_connection():
        conn = sqlite3.connect(forecasts_path)
        opened.append(conn)
        return conn

    def trade_connection(**kwargs):
        trade_connection_kwargs.append(dict(kwargs))
        conn = sqlite3.connect(trade_path)
        opened.append(conn)
        return conn

    def slow_portfolio(conn):
        first = [True]

        def mark_started():
            if first[0]:
                first[0] = False
                query_entered.set()
            return 0

        conn.create_function("reactor_construct_started", 0, mark_started)
        conn.execute(
            """
            WITH RECURSIVE slow(value) AS (
              SELECT reactor_construct_started()
              UNION ALL
              SELECT value + 1 FROM slow WHERE value < 100000000
            )
            SELECT SUM(value) FROM slow
            """
        ).fetchone()
        pytest.fail("slow construct SQL must be interrupted")

    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda *_args, **_kwargs: {
            "enabled": True,
            "event_writer_enabled": True,
            "forecast_snapshot_trigger_enabled": False,
            "day0_extreme_trigger_enabled": False,
            "reactor_construct_work_cut_seconds": 5.0 if preemption else 0.05,
        },
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(
        main,
        "_start_venue_background_maintenance_after_reactor_if_required",
        lambda: None,
    )
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(reactor_wake, "reactor_urgent_wake_revision", lambda: None)
    monkeypatch.setattr(bankroll_provider, "warm_from_collateral_snapshot", lambda: True)
    monkeypatch.setattr(db, "ZEUS_FORECASTS_DB_PATH", forecasts_path)
    monkeypatch.setattr(db, "get_world_connection", world_connection)
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", forecasts_connection)
    monkeypatch.setattr(db, "get_trade_connection_with_world_required", trade_connection)
    monkeypatch.setattr(db, "world_write_mutex", lambda: threading.Lock())
    monkeypatch.setattr(portfolio_module, "load_runtime_open_portfolio", slow_portfolio)
    monkeypatch.setattr(
        adapter_module,
        "event_bound_live_adapter_from_trade_conn",
        lambda *_args, **_kwargs: pytest.fail("deferred construct must not build a venue adapter"),
    )
    monkeypatch.setattr(
        reactor_module,
        "_durable_exact_held_sell_completion_pending",
        lambda: False,
    )
    monkeypatch.setattr(
        reactor_module,
        "_paused_entry_wake_should_park",
        lambda **_kwargs: False,
    )
    reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()

    def reserve_completion(**_kwargs):
        reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
        return True

    monkeypatch.setattr(
        reactor_module,
        "request_global_auction_completion",
        reserve_completion,
    )

    armer = None
    if preemption:
        def arm_monitor():
            assert query_entered.wait(2.0)
            monitor_pressure.set()

        armer = threading.Thread(target=arm_monitor)
        armer.start()

    lock = threading.Lock()
    started = time.monotonic()
    assert reactor_module.run_edli_event_reactor_cycle(
        active_lock=lock,
        held_position_monitor_debt_pending=monitor_pressure.is_set,
    ) is False
    elapsed = time.monotonic() - started
    if armer is not None:
        armer.join(timeout=2.0)
        assert not armer.is_alive()
    assert query_entered.is_set()
    assert elapsed < 2.0
    assert not lock.locked()
    bounded_trade_calls = [
        kwargs
        for kwargs in trade_connection_kwargs
        if kwargs.get("deadline_monotonic") is not None
    ]
    assert len(bounded_trade_calls) == 1
    assert 1 <= int(bounded_trade_calls[0]["busy_timeout_ms"]) <= 1000
    assert float(bounded_trade_calls[0]["deadline_monotonic"]) > started
    if preemption:
        due_at_start, unrelated_family_probe = (
            reactor_module._global_auction_monitor_cancellation_probe(
                lambda: False
            )
        )
        assert due_at_start is True
        assert unrelated_family_probe() is False
    reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def test_reactor_construct_normal_live_observed_cut_remains_buy_capable():
    from src.engine.global_auction_universe import WorkContext
    from src.events import reactor

    clock = [0.0]
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        due_at_start, monitor_cancelled = (
            reactor._global_auction_monitor_cancellation_probe(
                lambda: False,
                monitor_debt_pending=lambda: False,
            )
        )
        context = WorkContext(
            deadline_monotonic=reactor.DEFAULT_REACTOR_CONSTRUCT_WORK_CUT_SECONDS,
            cancel_requested=monitor_cancelled,
            monotonic=lambda: clock[0],
        )

        assert due_at_start is False
        clock[0] = 28.408
        assert context.checkpoint("reactor_construct:normal_buy") > 16.0
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set() is False
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_producer_fast_path_skips_metar_ledger_recovery_sync():
    from src.events.reactor import run_edli_event_reactor_cycle

    source = inspect.getsource(run_edli_event_reactor_cycle)
    recovery_sync = source[
        source.index("if not producer_fast_path:") : source.index(
            '_log_stage("day0_ledger_sync")'
        )
    ]

    assert "sync_from_ledger" in recovery_sync


def test_main_reactor_injects_day0_and_monitor_preemption_signals(
    monkeypatch,
):
    import src.events.reactor as reactor_module
    import src.main as main
    from src.runtime import reactor_wake

    captured = {}
    urgent_identity = ["wake-owned", "day0_extreme_event_committed"]

    def fake_run(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(main, "_start_edli_reactor_wake_listener", lambda: None)
    monkeypatch.setattr(reactor_module, "run_edli_event_reactor_cycle", fake_run)
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_identity",
        lambda: tuple(urgent_identity),
    )
    # This scheduler seam test owns handoff predicates, not live held-position
    # DB authority. Keep canonical debt isolated so a missing test DB cannot
    # manufacture a held-monitor preemption signal.
    monkeypatch.setattr(main, "_held_position_monitor_entry_block_reason", lambda: None)
    monkeypatch.setattr(main, "_held_position_monitor_debt_pending", lambda: False)
    main._day0_urgent_wake_pending.clear()
    main._day0_exit_monitor_attempts.clear()
    try:
        assert main._edli_event_reactor_cycle(
            producer_wake_reason="market_price_advanced",
            producer_wake_ids=("wake-owned",),
            producer_wake_published_at="2026-07-19T12:00:00+00:00",
            producer_wake_event_ids=("price-event",),
        ) is True
        assert captured["producer_wake_ids"] == ("wake-owned",)
        assert captured["producer_wake_published_at"] == (
            "2026-07-19T12:00:00+00:00"
        )
        assert captured["urgent_day0_pending"]() is False
        assert captured["held_position_monitor_pending"]() is False
        assert captured["held_position_monitor_debt_pending"]() is False
        main._held_position_monitor_handoff_pending.set()
        assert captured["held_position_monitor_pending"]() is False
        main._periodic_held_position_monitor_handoff_pending.set()
        assert captured["held_position_monitor_pending"]() is True
        main._periodic_held_position_monitor_fairness_debt.set()
        assert captured["held_position_monitor_debt_pending"]() is True
        main._periodic_held_position_monitor_handoff_pending.clear()
        main._periodic_held_position_monitor_fairness_debt.clear()
        main._held_position_monitor_handoff_pending.clear()
        main._day0_urgent_wake_pending.set()
        assert captured["urgent_day0_pending"]() is True
        main._day0_exit_monitor_attempts["wake-owned"] = None
        assert captured["urgent_day0_pending"]() is False
        urgent_identity[0] = "wake-new"
        assert captured["urgent_day0_pending"]() is True
    finally:
        main._periodic_held_position_monitor_handoff_pending.clear()
        main._periodic_held_position_monitor_fairness_debt.clear()
        main._held_position_monitor_handoff_pending.clear()
        main._held_position_monitor_canonical_debt.clear()
        main._day0_urgent_wake_pending.clear()
        main._day0_exit_monitor_attempts.clear()


def _stub_selected_day0_wake_poll(monkeypatch, main, reactor_wake, wake):
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: False)
    monkeypatch.setattr(
        reactor_wake,
        "read_reactor_wake",
        lambda **_kwargs: wake,
    )
    monkeypatch.setattr(
        reactor_wake,
        "coalescible_reactor_wakes",
        lambda selected: (selected,),
    )
    main._day0_exit_monitor_attempts.clear()
    main._forecast_exit_monitor_attempts.clear()
    main._edli_initialize_reactor_wake_cursor()


def test_pure_entry_day0_no_monitor_owns_sticky_urgent_identity(monkeypatch):
    import src.main as main
    from src.runtime import reactor_wake

    family = ("Paris", "2026-08-03", "high")
    wake = reactor_wake.ReactorWake(
        "day0-pure-entry",
        "2026-08-02T12:00:00+00:00",
        "day0",
        "day0_extreme_event_committed",
        forecast_families=(family,),
    )
    urgent_identity = ["forecast-next", "forecast_posterior_advanced"]
    _stub_selected_day0_wake_poll(monkeypatch, main, reactor_wake, wake)
    monkeypatch.setattr(
        main,
        "_day0_wake_requires_exit_monitor",
        lambda _families: False,
    )
    monkeypatch.setattr(
        main,
        "_pending_held_day0_wake_families",
        lambda: frozenset(),
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_identity",
        lambda: tuple(urgent_identity),
    )

    try:
        assert main._edli_reactor_wake_poll_once() is False
        assert main._day0_exit_monitor_attempt_state(wake.wake_id) == (True, True)
        assert main._day0_urgent_wake_pending.is_set() is True
        assert main._unowned_day0_urgent_wake_pending() is False
        assert wake.wake_id not in main._exit_monitor_excluded_wake_ids()

        urgent_identity[:] = ["day0-new", "day0_extreme_event_committed"]
        assert main._unowned_day0_urgent_wake_pending() is True

        urgent_identity[:] = ["forecast-next", "forecast_posterior_advanced"]
        monkeypatch.setattr(
            reactor_wake,
            "acknowledge_reactor_wake",
            lambda _wake: True,
        )
        assert main._acknowledge_edli_reactor_wake_batch(
            wake,
            (wake,),
            day0_wake=True,
        ) is True
        assert main._day0_exit_monitor_attempt_state(wake.wake_id) == (False, None)
    finally:
        main._day0_exit_monitor_attempts.clear()
        main._day0_urgent_wake_pending.clear()


@pytest.mark.parametrize(
    "requires_monitor,pending_held_families",
    [
        pytest.param(True, frozenset(), id="target-query-failure"),
        pytest.param(False, None, id="pending-proof-unknown"),
        pytest.param(
            False,
            frozenset({("Paris", "2026-08-03", "high")}),
            id="pending-held-family",
        ),
    ],
)
def test_day0_uncertain_or_held_monitor_proof_never_marks_no_monitor_complete(
    monkeypatch,
    requires_monitor,
    pending_held_families,
):
    import src.main as main
    from src.runtime import reactor_wake

    family = ("Paris", "2026-08-03", "high")
    wake = reactor_wake.ReactorWake(
        "day0-monitor-required",
        "2026-08-02T12:00:00+00:00",
        "day0",
        "day0_extreme_event_committed",
        forecast_families=(family,),
    )
    dispatched: list[str] = []
    _stub_selected_day0_wake_poll(monkeypatch, main, reactor_wake, wake)
    monkeypatch.setattr(
        main,
        "_day0_wake_requires_exit_monitor",
        lambda _families: requires_monitor,
    )
    monkeypatch.setattr(
        main,
        "_pending_held_day0_wake_families",
        lambda: pending_held_families,
    )

    def dispatch_monitor(wake_id, _families):
        dispatched.append(wake_id)
        return False

    monkeypatch.setattr(
        main,
        "_dispatch_day0_exit_monitor",
        dispatch_monitor,
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_identity",
        lambda: ("forecast-next", "forecast_posterior_advanced"),
    )

    try:
        assert main._edli_reactor_wake_poll_once() is False
        assert main._day0_exit_monitor_attempt_state(wake.wake_id) == (False, None)
        assert dispatched == [wake.wake_id]
        assert main._unowned_day0_urgent_wake_pending() is True
    finally:
        main._day0_exit_monitor_attempts.clear()
        main._day0_urgent_wake_pending.clear()


def test_day0_completed_ownership_marker_clears_on_listener_restart():
    import src.main as main

    main._day0_exit_monitor_attempts.clear()
    try:
        main._day0_exit_monitor_attempts["day0-served"] = True
        main._day0_exit_monitor_attempts["day0-running"] = None

        excluded = main._exit_monitor_excluded_wake_ids()
        assert "day0-served" not in excluded
        assert "day0-running" in excluded

        main._edli_initialize_reactor_wake_cursor()
        assert "day0-served" not in main._day0_exit_monitor_attempts
        assert main._day0_exit_monitor_attempts["day0-running"] is None
    finally:
        main._day0_exit_monitor_attempts.clear()


def test_monitor_preempts_once_then_next_auction_must_complete(monkeypatch):
    from types import SimpleNamespace

    from src.events import reactor
    from src.runtime import reactor_wake

    pending = [True]
    monkeypatch.setattr(reactor_wake, "reactor_wakes_since", lambda _at: ())
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **_kwargs: None,
    )
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        due_at_start, first_probe = (
            reactor._global_auction_monitor_cancellation_probe(
                lambda: pending[0]
            )
        )
        assert due_at_start is False
        assert first_probe() is True
        assert first_probe() is True

        due_at_start, completion_probe = (
            reactor._global_auction_monitor_cancellation_probe(
                lambda: pending[0]
            )
        )
        assert due_at_start is True
        assert completion_probe() is False
        reactor._settle_global_auction_monitor_fairness(
            completion_due_at_start=due_at_start,
            result=SimpleNamespace(
                processed=1,
                proof_accepted=1,
                rejected=0,
                retried=0,
                global_auction_completed_non_cancelled=1,
                rejection_reasons=[],
            ),
        )
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set() is False
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_monitor_does_not_preempt_when_completion_wake_is_not_durable(
    monkeypatch,
):
    from src.events import reactor

    monkeypatch.setattr(
        reactor,
        "request_global_auction_completion",
        lambda **_kwargs: False,
    )
    due_at_start, cancellation_probe = (
        reactor._global_auction_monitor_cancellation_probe(lambda: True)
    )

    assert due_at_start is False
    assert cancellation_probe() is False
    assert cancellation_probe() is False


def test_monitor_fairness_debt_cancels_reserved_completion_without_new_wake(
    monkeypatch,
):
    from src.events import reactor

    monkeypatch.setattr(
        reactor,
        "request_global_auction_completion",
        lambda **_kwargs: pytest.fail("reserved completion debt must not duplicate"),
    )
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        due_at_start, cancellation_probe = (
            reactor._global_auction_monitor_cancellation_probe(
                lambda: True,
                monitor_debt_pending=lambda: True,
                completion_due=True,
            )
        )
        assert due_at_start is True
        assert cancellation_probe() is True
        assert cancellation_probe() is True
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set()
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_monitor_fairness_debt_reserves_completion_before_cancelling(
    monkeypatch,
):
    from src.events import reactor

    reservations: list[str] = []
    monkeypatch.setattr(
        reactor,
        "request_global_auction_completion",
        lambda **kwargs: reservations.append(kwargs["reason"]) or True,
    )
    due_at_start, cancellation_probe = (
        reactor._global_auction_monitor_cancellation_probe(
            lambda: False,
            monitor_debt_pending=lambda: True,
        )
    )

    assert due_at_start is False
    assert cancellation_probe() is True
    assert cancellation_probe() is True
    assert reservations == ["periodic_monitor_preemption"]


def test_monitor_fairness_debt_probe_failure_cannot_veto_auction(monkeypatch):
    from src.events import reactor

    monkeypatch.setattr(
        reactor,
        "request_global_auction_completion",
        lambda **_kwargs: pytest.fail("failed scheduler hint must not reserve debt"),
    )
    due_at_start, cancellation_probe = (
        reactor._global_auction_monitor_cancellation_probe(
            lambda: False,
            monitor_debt_pending=lambda: (_ for _ in ()).throw(
                RuntimeError("debt hint unavailable")
            ),
        )
    )

    assert due_at_start is False
    assert cancellation_probe() is False


def test_held_sell_completion_request_persists_position_q_and_bid_witness(tmp_path):
    from src.events import reactor
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        assert reactor.request_global_auction_completion(
            reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
            position_id="position-1",
            family=("Paris", "2026-07-28", "LOW"),
            probability_content_identity="q-content-1",
            held_token_id="token-no-1",
            held_best_bid=0.12,
            bid_observed_at="2026-07-28T08:00:00+00:00",
            wake_path=path,
        ) is True
        assert reactor.request_global_auction_completion(
            reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
            position_id="position-1",
            family=("Paris", "2026-07-28", "low"),
            probability_content_identity="q-content-1",
            held_token_id="token-no-1",
            held_best_bid=0.12,
            bid_observed_at="2026-07-28T08:00:00+00:00",
            wake_path=path,
        ) is True
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set() is True
        wakes = reactor_wake.reactor_wakes_since(None, path=path)
        assert len(wakes) == 1
        assert wakes[0].source == "held_position_monitor"
        assert wakes[0].reason == (
            "held_sell_global_auction_completion_requested"
        )
        assert wakes[0].forecast_families == (
            ("Paris", "2026-07-28", "low"),
        )
        request = wakes[0].held_sell_reauction_requests[0]
        assert request.position_id == "position-1"
        assert request.family == ("Paris", "2026-07-28", "low")
        assert request.probability_content_identity == "q-content-1"
        assert request.held_token_id == "token-no-1"
        assert request.held_best_bid == 0.12
        assert request.bid_observed_at == "2026-07-28T08:00:00+00:00"
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


@pytest.mark.parametrize(
    ("held_best_bid", "expected_book_state"),
    (
        (0.03, "NO_EXECUTABLE_BOOK"),
        (0.96, "EXECUTABLE"),
        (0.98, "EXECUTABLE"),
        (0.999, "EXECUTABLE"),
        (0.05, "EXECUTABLE"),
        (0.95, "EXECUTABLE"),
    ),
)
def test_held_sell_completion_infers_probability_domain_book_state(
    monkeypatch,
    held_best_bid,
    expected_book_state,
):
    from types import SimpleNamespace

    from src.events import reactor
    from src.runtime import reactor_wake

    wakes = []
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **kwargs: wakes.append(kwargs) or SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(reactor_wake, "reactor_wakes_since", lambda _at: ())
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        assert reactor.request_global_auction_completion(
            reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
            position_id=f"position-band-{held_best_bid}",
            family=("Tokyo", "2026-07-30", "low"),
            probability_content_identity="q-content-band",
            held_token_id="token-band",
            held_best_bid=held_best_bid,
            bid_observed_at="2026-07-30T01:00:00+00:00",
            schema_version=3,
        ) is True
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()

    assert len(wakes) == 1
    request = wakes[0]["held_sell_reauction_requests"][0]
    assert request.book_state == expected_book_state
    assert request.held_best_bid == held_best_bid
    assert request.probability_content_identity == "q-content-band"
    assert request.held_token_id == "token-band"


def test_held_sell_completion_request_survives_wake_io_failure(monkeypatch):
    from types import SimpleNamespace

    from src.events import reactor
    from src.runtime import reactor_wake

    attempts = []
    durable_wakes = []

    def flaky_publish(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise OSError("wake path unavailable")
        durable_wakes.append(
            SimpleNamespace(
                reason=kwargs["reason"],
                forecast_families=kwargs["forecast_families"],
            )
        )

    monkeypatch.setattr(reactor_wake, "publish_reactor_wake", flaky_publish)
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda _at: tuple(durable_wakes),
    )
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        assert reactor.request_global_auction_completion(
            reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
            position_id="position-1",
            family=("Paris", "2026-07-28", "low"),
        ) is False
        assert reactor.request_global_auction_completion(
            reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
            position_id="position-1",
            family=("Paris", "2026-07-28", "low"),
        ) is True
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set() is True
        assert len(attempts) == 2
        assert len(durable_wakes) == 1
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_typed_held_sell_completion_rejects_queue_read_failure(monkeypatch):
    from src.events import reactor
    from src.runtime import reactor_wake

    published = []
    monkeypatch.setattr(
        reactor_wake,
        "latest_v4_held_sell_reauction_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("queue unavailable")
        ),
    )
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **kwargs: published.append(kwargs),
    )
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        accepted, request = reactor.request_global_auction_completion(
            reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
            position_id="position-queue-failed",
            family=("Paris", "2026-07-30", "low"),
            probability_content_identity="q-content-queue-failed",
            held_token_id="token-no-queue-failed",
            held_best_bid=0.12,
            bid_observed_at="2026-07-30T08:00:00+00:00",
            return_request=True,
        )
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()

    assert accepted is False
    assert request is None
    assert published == []


def test_held_sell_reauction_typed_reject_receipt_completes_request(tmp_path):
    from src.runtime.reactor_wake import (
        HeldSellReauctionReceipt,
        held_sell_reauction_requests_completed,
        make_held_sell_reauction_request,
        persist_held_sell_reauction_receipts,
    )

    request = make_held_sell_reauction_request(
        position_id="position-reject",
        family=("Paris", "2026-07-28", "low"),
        probability_content_identity="q-content-reject",
        held_token_id="token-no-reject",
        held_best_bid=0.09,
        bid_observed_at="2026-07-28T08:00:00+00:00",
    )

    assert persist_held_sell_reauction_receipts(
        (
            HeldSellReauctionReceipt(
                request_id=request.request_id,
                material_identity=request.material_identity,
                generation=request.generation,
                status="REJECTED",
                reason="GLOBAL_AUCTION_CURRENT_HOLDING_REJECTED:CASH_DOMINATES",
            ),
        ),
        path=tmp_path / "wake.json",
    ) is True
    assert held_sell_reauction_requests_completed(
        (request,), path=tmp_path / "wake.json"
    ) is True


def test_parent_v1_held_sell_request_hash_and_receipt_remain_compatible(tmp_path):
    """A pre-V2 durable wake must survive parsing and retain its original ID."""
    import hashlib
    import json

    from src.runtime import reactor_wake

    material = {
        "position_id": "legacy-v1-position",
        "family": ("Paris", "2026-07-28", "low"),
        "probability_content_identity": "legacy-v1-q",
        "held_token_id": "legacy-v1-token",
        "held_best_bid": 0.17,
        "bid_observed_at": "2026-07-28T08:00:00+00:00",
    }
    material_identity = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    generation = "legacy-v1-generation"
    request_id = hashlib.sha256(
        json.dumps(
            {
                "generation": generation,
                "material_identity": material_identity,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    parsed = reactor_wake._clean_held_sell_reauction_requests(
        (
            {
                **material,
                "request_id": request_id,
                "material_identity": material_identity,
                "generation": generation,
            },
        )
    )

    assert len(parsed) == 1
    assert parsed[0].schema_version == 1
    assert parsed[0].material_identity == material_identity
    assert parsed[0].request_id == request_id
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (
            reactor_wake.HeldSellReauctionReceipt(
                request_id=request_id,
                material_identity=material_identity,
                generation=generation,
                status="REJECTED",
                reason="GLOBAL_AUCTION_CURRENT_HOLDING_REJECTED:CASH_DOMINATES",
            ),
        ),
        path=tmp_path / "wake.json",
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        parsed, path=tmp_path / "wake.json"
    )


def test_parent_v2_held_sell_request_and_receipt_remain_compatible(tmp_path):
    """Schema 3 must not reinterpret already-durable schema-2 identities."""
    import hashlib
    import json

    from src.runtime import reactor_wake

    family = ("Lucknow", "2026-07-29", "high")
    scope_identity = reactor_wake.held_sell_reauction_scope_identity(
        position_id="legacy-v2-position",
        family=family,
        probability_content_identity="legacy-v2-q",
        held_token_id="legacy-v2-token",
        schema_version=2,
    )
    generation = "legacy-v2-generation"
    request_id = hashlib.sha256(
        json.dumps(
            {
                "generation": generation,
                "material_identity": scope_identity,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    parsed = reactor_wake._clean_held_sell_reauction_requests(
        (
            {
                "request_id": request_id,
                "material_identity": scope_identity,
                "generation": generation,
                "position_id": "legacy-v2-position",
                "family": family,
                "probability_content_identity": "legacy-v2-q",
                "held_token_id": "legacy-v2-token",
                "held_best_bid": 0.19,
                "bid_observed_at": "2026-07-29T12:00:00+00:00",
                "schema_version": 2,
                "scope_identity": scope_identity,
                "book_state": "EXECUTABLE",
                "probability_observed_at": "2026-07-29T12:00:00+00:00",
            },
        )
    )

    assert len(parsed) == 1
    assert parsed[0].schema_version == 2
    assert parsed[0].attempt_identity == ""
    assert parsed[0].request_id == request_id
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (
            reactor_wake.HeldSellReauctionReceipt(
                request_id=request_id,
                material_identity=scope_identity,
                generation=generation,
                schema_version=2,
                scope_identity=scope_identity,
                book_state="EXECUTABLE",
                status="ACTUATED",
                reason="legacy-v2-actuated",
                selection_epoch_identity="legacy-v2-epoch",
                sell_book_witness_identity="legacy-v2-book",
                answered_probability_content_identity="legacy-v2-q",
            ),
        ),
        path=tmp_path / "wake.json",
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        parsed, path=tmp_path / "wake.json"
    )


def test_lucknow_zero_bid_v3_obligation_is_durable_but_cannot_ack(tmp_path):
    """A zero bid is a durable redecision debt, never an executable SELL price."""
    from src.runtime.reactor_wake import (
        HeldSellReauctionReceipt,
        held_sell_reauction_requests_completed,
        make_held_sell_reauction_request,
        persist_held_sell_reauction_receipts,
        publish_reactor_wake,
        reactor_wakes_since,
    )

    path = tmp_path / "wake.json"
    request = make_held_sell_reauction_request(
        position_id="lucknow-bid-zero",
        family=("Lucknow", "2026-07-29", "high"),
        probability_content_identity="q-lucknow-current",
        held_token_id="token-lucknow-no",
        held_best_bid=0.0,
        bid_observed_at="2026-07-29T12:00:00+00:00",
        schema_version=3,
        book_state="NO_EXECUTABLE_BOOK",
        generation="lucknow-zero-generation",
    )
    publish_reactor_wake(
        source="held_position_monitor",
        reason="held_sell_global_auction_completion_requested",
        path=path,
        held_sell_reauction_requests=(request,),
    )

    stored = reactor_wakes_since(None, path=path)
    assert stored[0].held_sell_reauction_requests == (request,)
    assert request.held_best_bid == 0.0
    assert request.book_state == "NO_EXECUTABLE_BOOK"
    assert persist_held_sell_reauction_receipts(
        (
            HeldSellReauctionReceipt(
                request_id=request.request_id,
                material_identity=request.material_identity,
                generation=request.generation,
                status="REJECTED",
                reason="legacy_generic_reject_must_not_ack_v3",
            ),
        ),
        path=path,
    ) is False
    assert not held_sell_reauction_requests_completed((request,), path=path)


def test_lucknow_no_book_generation_completes_only_from_fresh_same_q_book(monkeypatch, tmp_path):
    """A later book answers the V3 debt without reusing an old attempt."""
    from src.events import reactor
    from src.runtime.reactor_wake import (
        held_sell_reauction_requests_completed,
        make_held_sell_reauction_request,
        persist_held_sell_reauction_receipts,
    )

    kwargs = {
        "position_id": "lucknow-no-book-generation",
        "family": ("Lucknow", "2026-07-29", "high"),
        "probability_content_identity": "q-lucknow-current",
        "held_token_id": "token-lucknow-no-book",
        "schema_version": 3,
        "generation": "lucknow-stable-generation",
    }
    original = make_held_sell_reauction_request(
        **kwargs,
        held_best_bid=0.0,
        bid_observed_at="2026-07-29T12:00:00+00:00",
        book_state="NO_EXECUTABLE_BOOK",
    )
    fresh = make_held_sell_reauction_request(
        **kwargs,
        held_best_bid=0.21,
        bid_observed_at="2026-07-29T12:01:00+00:00",
        probability_observed_at="2026-07-29T12:01:00+00:00",
        book_state="EXECUTABLE",
    )
    assert fresh.material_identity == original.material_identity == original.scope_identity
    assert fresh.generation == original.generation
    assert fresh.attempt_identity != original.attempt_identity
    assert fresh.request_id != original.request_id

    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(original,),
        result=_held_sell_completion_result(
            position_id=original.position_id,
            token_id=original.held_token_id,
            probability_content_identity="q-lucknow-current",
        ),
    )
    assert len(receipts) == 1
    assert receipts[0].request_id == original.request_id
    assert receipts[0].answered_probability_content_identity == "q-lucknow-current"
    assert persist_held_sell_reauction_receipts(
        receipts, path=tmp_path / "wake.json"
    ) is True
    assert held_sell_reauction_requests_completed(
        (original,), path=tmp_path / "wake.json"
    ) is True
    assert held_sell_reauction_requests_completed(
        (fresh,), path=tmp_path / "wake.json"
    ) is False

    fresh_receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(fresh,),
        result=_held_sell_completion_result(
            position_id=fresh.position_id,
            token_id=fresh.held_token_id,
            probability_content_identity="q-lucknow-current",
        ),
    )
    assert persist_held_sell_reauction_receipts(
        fresh_receipts, path=tmp_path / "wake.json"
    ) is True
    assert held_sell_reauction_requests_completed(
        (fresh,), path=tmp_path / "wake.json"
    ) is True


@pytest.mark.parametrize("status", ("ACTUATED", "CAPITAL_REJECTED"))
def test_v3_old_attempt_receipt_cannot_ack_fresh_context(tmp_path, status):
    from src.runtime import reactor_wake

    common = {
        "position_id": f"old-attempt-{status.lower()}",
        "family": ("Lucknow", "2026-07-29", "high"),
        "held_token_id": f"token-{status.lower()}",
        "schema_version": 3,
        "generation": "same-obligation-generation",
    }
    old = reactor_wake.make_held_sell_reauction_request(
        **common,
        probability_content_identity="q-old",
        held_best_bid=0.0,
        bid_observed_at="2026-07-29T12:00:00+00:00",
        book_state="NO_EXECUTABLE_BOOK",
    )
    fresh = reactor_wake.make_held_sell_reauction_request(
        **common,
        scope_identity=old.scope_identity,
        probability_content_identity="q-fresh",
        probability_observed_at="2026-07-29T12:01:00+00:00",
        held_best_bid=0.23,
        bid_observed_at="2026-07-29T12:01:00+00:00",
        book_state="EXECUTABLE",
    )
    assert old.generation == fresh.generation
    assert old.material_identity == fresh.material_identity
    assert old.request_id != fresh.request_id

    receipt = reactor_wake.HeldSellReauctionReceipt(
        request_id=old.request_id,
        material_identity=old.material_identity,
        generation=old.generation,
        schema_version=3,
        scope_identity=old.scope_identity,
        book_state="EXECUTABLE",
        status=status,
        reason=f"old-{status.lower()}",
        selection_epoch_identity="old-epoch",
        sell_book_witness_identity="old-book",
        capital_objective_proof=(
            "old-capital-proof" if status == "CAPITAL_REJECTED" else ""
        ),
        answered_probability_content_identity="q-old",
        attempt_identity=old.attempt_identity,
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (receipt,), path=tmp_path / "wake.json"
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (old,), path=tmp_path / "wake.json"
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (fresh,), path=tmp_path / "wake.json"
    )


def test_completed_old_attempt_cannot_starve_coalesced_fresh_receipt(tmp_path):
    from src.runtime import reactor_wake

    common = {
        "position_id": "coalesced-attempts",
        "family": ("Lucknow", "2026-07-29", "high"),
        "held_token_id": "coalesced-token",
        "schema_version": 3,
        "generation": "coalesced-generation",
    }
    old = reactor_wake.make_held_sell_reauction_request(
        **common,
        probability_content_identity="q-old",
        held_best_bid=0.0,
        bid_observed_at="2026-07-29T12:00:00+00:00",
        book_state="NO_EXECUTABLE_BOOK",
    )
    fresh = reactor_wake.make_held_sell_reauction_request(
        **common,
        scope_identity=old.scope_identity,
        probability_content_identity="q-fresh",
        probability_observed_at="2026-07-29T12:01:00+00:00",
        held_best_bid=0.23,
        bid_observed_at="2026-07-29T12:01:00+00:00",
        book_state="EXECUTABLE",
    )

    def receipt(request, suffix):
        return reactor_wake.HeldSellReauctionReceipt(
            request_id=request.request_id,
            material_identity=request.material_identity,
            generation=request.generation,
            schema_version=3,
            scope_identity=request.scope_identity,
            book_state="EXECUTABLE",
            status="ACTUATED",
            reason=f"actuated-{suffix}",
            selection_epoch_identity=f"epoch-{suffix}",
            sell_book_witness_identity=f"book-{suffix}",
            answered_probability_content_identity=f"q-{suffix}",
            attempt_identity=request.attempt_identity,
        )

    path = tmp_path / "wake.json"
    first_old = receipt(old, "old-first")
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (first_old,), path=path
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (receipt(old, "old-reanswered"), receipt(fresh, "fresh")),
        path=path,
    )
    assert reactor_wake._read_held_sell_reauction_receipt(
        old.request_id, path=path
    ) == first_old
    assert reactor_wake.held_sell_reauction_requests_completed(
        (old, fresh), path=path
    )


def test_v3_no_book_wake_upgrades_same_generation_on_fresh_superseding_q(monkeypatch, tmp_path):
    """A fresh book/q republishes the original debt, never a stale-q action."""
    from types import SimpleNamespace

    from src.events import reactor
    from src.runtime import reactor_wake

    published = []

    def publish(**kwargs):
        published.append(
            SimpleNamespace(
                reason=kwargs["reason"],
                forecast_families=kwargs["forecast_families"],
                held_sell_reauction_requests=kwargs["held_sell_reauction_requests"],
            )
        )
        return published[-1]

    monkeypatch.setattr(reactor_wake, "publish_reactor_wake", publish)
    monkeypatch.setattr(reactor_wake, "reactor_wakes_since", lambda _at: tuple(published))
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        common = {
            "reason": "GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED",
            "position_id": "lucknow-q-supersession",
            "family": ("Lucknow", "2026-07-29", "high"),
            "held_token_id": "token-lucknow-q-supersession",
            "schema_version": 3,
        }
        assert reactor.request_global_auction_completion(
            **common,
            probability_content_identity="q-trigger-old",
            held_best_bid=0.0,
            bid_observed_at="2026-07-29T12:00:00+00:00",
            book_state="NO_EXECUTABLE_BOOK",
        ) is True
        original = published[0].held_sell_reauction_requests[0]
        assert reactor.request_global_auction_completion(
            **common,
            probability_content_identity="q-current-new",
            probability_observed_at="2026-07-29T12:01:00+00:00",
            held_best_bid=0.24,
            bid_observed_at="2026-07-29T12:01:00+00:00",
            book_state="EXECUTABLE",
        ) is True
        assert len(published) == 2
        refreshed = published[1].held_sell_reauction_requests[0]
        assert refreshed.scope_identity == original.scope_identity
        assert refreshed.generation == original.generation
        assert refreshed.attempt_identity != original.attempt_identity
        assert refreshed.request_id != original.request_id
        assert refreshed.probability_content_identity == "q-current-new"

        receipts = reactor._held_sell_reauction_receipts_from_global_cut(
            requests=(refreshed,),
            result=_held_sell_completion_result(
                position_id=refreshed.position_id,
                token_id=refreshed.held_token_id,
                probability_content_identity="q-current-new",
            ),
        )
        assert receipts[0].answered_probability_content_identity == "q-current-new"
        assert reactor_wake.persist_held_sell_reauction_receipts(
            receipts, path=tmp_path / "wake.json"
        ) is True
        assert reactor_wake.held_sell_reauction_requests_completed(
            (refreshed,), path=tmp_path / "wake.json"
        ) is True
        assert reactor_wake.held_sell_reauction_requests_completed(
            (original,), path=tmp_path / "wake.json"
        ) is False
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_v4_fresh_q_attempts_reuse_one_debt_and_complete_latest_action(tmp_path):
    """42 fresh q witnesses retain one held SELL debt across a restart."""
    from src.events import reactor
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    attempts = []
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        common = {
            "reason": "GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
            "position_id": "7ba16223-79c",
            "family": ("Istanbul", "2026-08-02", "high"),
            "held_token_id": "istanbul-held-no",
            "schema_version": 4,
            "held_best_bid": 0.22,
            "book_state": "EXECUTABLE",
        }
        for index in range(42):
            observed_at = f"2026-08-02T12:{index:02d}:00+00:00"
            accepted, request = reactor.request_global_auction_completion(
                **common,
                probability_content_identity=f"q-istanbul-{index}",
                probability_observed_at=observed_at,
                bid_observed_at=observed_at,
                wake_path=path,
                return_request=True,
            )
            assert accepted is True
            assert request is not None
            attempts.append(request)

        attempts = tuple(attempts)
        assert len(attempts) == 42
        assert {request.scope_identity for request in attempts} == {
            attempts[0].scope_identity
        }
        assert {request.generation for request in attempts} == {attempts[0].generation}
        assert {request.request_id for request in attempts} == {attempts[0].request_id}
        assert len({request.attempt_identity for request in attempts}) == 1
        assert attempts[0].scope_identity != reactor_wake.held_sell_reauction_scope_identity(
            position_id="7ba16223-79c",
            family=("Istanbul", "2026-08-02", "high"),
            probability_content_identity="q-istanbul-41",
            held_token_id="istanbul-held-yes",
            schema_version=4,
        )

        queued = reactor_wake.reactor_wakes_since(None, path=path)
        assert len(queued) == 1
        restarted = queued[0]
        request = restarted.held_sell_reauction_requests[0]
        assert request == attempts[0]

        receipts = reactor._held_sell_reauction_receipts_from_global_cut(
            requests=(request,),
            result=_held_sell_completion_result(
                position_id=request.position_id,
                token_id=request.held_token_id,
                probability_content_identity="q-istanbul-41",
            ),
        )
        assert receipts[0].status == "ACTUATED"
        assert receipts[0].request_id == request.request_id
        assert receipts[0].attempt_identity == request.attempt_identity
        assert receipts[0].answered_probability_content_identity == "q-istanbul-41"
        assert reactor_wake.persist_held_sell_reauction_receipts(receipts, path=path)
        assert reactor_wake.held_sell_reauction_requests_completed(
            (request,), path=path
        )
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def _v4_stable_debt_attempts(
    tmp_path,
    *,
    publish_old=True,
    publish_fresh=True,
):
    """Publish deterministic old/new witnesses for one V4 stable debt."""
    from src.runtime import reactor_wake

    common = {
        "position_id": "istanbul-v4-lineage",
        "family": ("Istanbul", "2026-08-02", "high"),
        "held_token_id": "istanbul-v4-no",
        "schema_version": 4,
        "generation": "istanbul-v4-stable-generation",
        "held_best_bid": 0.22,
        "book_state": "EXECUTABLE",
    }
    old = reactor_wake.make_held_sell_reauction_request(
        **common,
        probability_content_identity="q-old",
        probability_observed_at="2026-08-02T12:00:00+00:00",
        bid_observed_at="2026-08-02T12:00:00+00:00",
    )
    fresh = reactor_wake.make_held_sell_reauction_request(
        **common,
        probability_content_identity="q-fresh",
        probability_observed_at="2026-08-02T12:01:00+00:00",
        bid_observed_at="2026-08-02T12:01:00+00:00",
    )
    assert old.request_id == fresh.request_id
    assert old.generation == fresh.generation
    assert old.attempt_identity != fresh.attempt_identity
    path = tmp_path / "wake.json"
    attempts = []
    if publish_old:
        attempts.append((old, datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)))
    if publish_fresh:
        attempts.append(
            (fresh, datetime(2026, 8, 2, 12, 1, tzinfo=timezone.utc))
        )
    for request, published_at in attempts:
        reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=path,
            published_at=published_at,
            held_sell_reauction_requests=(request,),
        )
    return path, old, fresh


def _v4_actuated_receipt(request):
    from src.events import reactor

    return reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=_held_sell_completion_result(
            position_id=request.position_id,
            token_id=request.held_token_id,
            probability_content_identity=request.probability_content_identity,
        ),
    )[0]


def test_v4_old_receipt_after_new_witness_cannot_complete_current_attempt(tmp_path):
    from src.runtime import reactor_wake

    path, old, fresh = _v4_stable_debt_attempts(tmp_path)
    old_receipt = _v4_actuated_receipt(old)
    assert reactor_wake.persist_held_sell_reauction_receipts((old_receipt,), path=path)
    assert reactor_wake._read_held_sell_reauction_receipt(
        old.request_id,
        path=path,
        attempt_identity=old.attempt_identity,
    ) == old_receipt
    assert not reactor_wake.held_sell_reauction_requests_completed((old,), path=path)
    assert not reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)

    fresh_receipt = _v4_actuated_receipt(fresh)
    assert reactor_wake.persist_held_sell_reauction_receipts((fresh_receipt,), path=path)
    assert not reactor_wake.held_sell_reauction_requests_completed((old,), path=path)
    assert reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)


def test_v4_new_receipt_then_old_receipt_cannot_roll_back_current_lineage(tmp_path):
    from src.runtime import reactor_wake

    path, old, fresh = _v4_stable_debt_attempts(tmp_path)
    fresh_receipt = _v4_actuated_receipt(fresh)
    assert reactor_wake.persist_held_sell_reauction_receipts((fresh_receipt,), path=path)
    assert reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)

    old_receipt = _v4_actuated_receipt(old)
    assert reactor_wake.persist_held_sell_reauction_receipts((old_receipt,), path=path)
    assert reactor_wake._read_held_sell_reauction_receipt(
        fresh.request_id,
        path=path,
        attempt_identity=fresh.attempt_identity,
    ) == fresh_receipt
    assert reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)
    assert reactor_wake._read_held_sell_reauction_receipt(
        old.request_id,
        path=path,
        attempt_identity=old.attempt_identity,
    ) == old_receipt
    lineage = reactor_wake._read_v4_held_sell_reauction_lineage(
        fresh.scope_identity,
        path=path,
    )
    assert lineage is not None
    assert lineage.latest_attempt_identity == fresh.attempt_identity


def test_v4_concurrent_receipt_writers_preserve_both_attempts(tmp_path):
    from src.runtime import reactor_wake

    path, old, fresh = _v4_stable_debt_attempts(tmp_path)
    barrier = threading.Barrier(2)
    results = []
    failures = []

    def persist(receipt):
        try:
            barrier.wait(timeout=2.0)
            results.append(
                reactor_wake.persist_held_sell_reauction_receipts((receipt,), path=path)
            )
        except Exception as exc:  # pragma: no cover - assertions report worker failure.
            failures.append(exc)

    threads = tuple(
        threading.Thread(target=persist, args=(receipt,))
        for receipt in (_v4_actuated_receipt(old), _v4_actuated_receipt(fresh))
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)

    assert not any(thread.is_alive() for thread in threads)
    assert failures == []
    assert results == [True, True]
    assert reactor_wake._read_held_sell_reauction_receipt(
        old.request_id,
        path=path,
        attempt_identity=old.attempt_identity,
    ) == _v4_actuated_receipt(old)
    assert reactor_wake._read_held_sell_reauction_receipt(
        fresh.request_id,
        path=path,
        attempt_identity=fresh.attempt_identity,
    ) == _v4_actuated_receipt(fresh)
    assert reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)


def test_v4_lineage_latest_marker_fences_old_attempt_after_wake_ack(tmp_path):
    from src.runtime import reactor_wake

    path, old, fresh = _v4_stable_debt_attempts(tmp_path)
    fresh_receipt = _v4_actuated_receipt(fresh)
    assert reactor_wake.persist_held_sell_reauction_receipts((fresh_receipt,), path=path)
    assert reactor_wake.acknowledge_reactor_wakes(
        reactor_wake.reactor_wakes_since(None, path=path),
        path=path,
    )
    assert not reactor_wake.held_sell_reauction_requests_completed((old,), path=path)
    assert reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)


def test_v4_ack_revalidates_after_new_attempt_interleaves(tmp_path):
    from src.runtime import reactor_wake

    path, old, fresh = _v4_stable_debt_attempts(
        tmp_path,
        publish_fresh=False,
    )
    old_wake = reactor_wake.reactor_wakes_since(None, path=path)[0]
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (_v4_actuated_receipt(old),),
        path=path,
    )
    assert reactor_wake.held_sell_reauction_requests_completed((old,), path=path)

    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        published_at=datetime(2026, 8, 2, 12, 1, tzinfo=timezone.utc),
        held_sell_reauction_requests=(fresh,),
    )

    assert not reactor_wake.acknowledge_reactor_wake(old_wake, path=path)
    queued = reactor_wake.reactor_wakes_since(None, path=path)
    assert len(queued) == 1
    assert queued[0].held_sell_reauction_requests == (fresh,)
    assert not reactor_wake.held_sell_reauction_requests_completed((old,), path=path)

    assert reactor_wake.persist_held_sell_reauction_receipts(
        (_v4_actuated_receipt(fresh),),
        path=path,
    )
    assert reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)
    assert reactor_wake.acknowledge_reactor_wake(queued[0], path=path)


def test_v4_publish_fence_prevents_legacy_fallback_regression(
    monkeypatch,
    tmp_path,
):
    from src.runtime import reactor_wake

    path, old, fresh = _v4_stable_debt_attempts(
        tmp_path,
        publish_old=False,
        publish_fresh=False,
    )
    legacy_path = reactor_wake._wake_path(path)
    real_atomic_write = reactor_wake._atomic_write_wake
    real_lineage_locks = reactor_wake._held_sell_reauction_lineage_locks
    a_at_fallback = threading.Event()
    release_a = threading.Event()
    b_attempted_fence = threading.Event()
    b_finished = threading.Event()
    failures = []

    @contextmanager
    def observed_lineage_locks(scope_identities, *, path=None):
        if threading.current_thread().name == "v4-publisher-b":
            b_attempted_fence.set()
        with real_lineage_locks(
            scope_identities,
            path=path,
            timeout_seconds=2.0,
        ):
            yield

    def ordered_atomic_write(target, wake):
        if target == legacy_path and wake.wake_id == "wake-a":
            a_at_fallback.set()
            if not release_a.wait(timeout=2.0):
                raise TimeoutError("publisher A fallback was not released")
        real_atomic_write(target, wake)

    monkeypatch.setattr(
        reactor_wake,
        "_held_sell_reauction_lineage_locks",
        observed_lineage_locks,
    )
    monkeypatch.setattr(reactor_wake, "_atomic_write_wake", ordered_atomic_write)

    def publish(request, wake_id, published_at):
        try:
            reactor_wake.publish_reactor_wake(
                source="held_position_monitor",
                reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
                path=path,
                wake_id=wake_id,
                published_at=published_at,
                held_sell_reauction_requests=(request,),
            )
        except Exception as exc:  # pragma: no cover - assertion reports failure.
            failures.append(exc)
        finally:
            if wake_id == "wake-b":
                b_finished.set()

    publisher_a = threading.Thread(
        name="v4-publisher-a",
        target=publish,
        args=(old, "wake-a", datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)),
    )
    publisher_b = threading.Thread(
        name="v4-publisher-b",
        target=publish,
        args=(fresh, "wake-b", datetime(2026, 8, 2, 12, 1, tzinfo=timezone.utc)),
    )
    publisher_a.start()
    assert a_at_fallback.wait(timeout=2.0)
    a_holds_fence = reactor_wake._HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.locked()
    publisher_b.start()
    assert b_attempted_fence.wait(timeout=2.0)
    if not a_holds_fence:
        assert b_finished.wait(timeout=2.0)
    release_a.set()
    publisher_a.join(timeout=2.0)
    publisher_b.join(timeout=2.0)

    assert a_holds_fence
    assert not publisher_a.is_alive()
    assert not publisher_b.is_alive()
    assert failures == []
    lineage = reactor_wake._read_v4_held_sell_reauction_lineage(
        fresh.scope_identity,
        path=path,
    )
    assert lineage is not None
    assert lineage.latest_wake_id == "wake-b"
    queued = reactor_wake.reactor_wakes_since(None, path=path)
    assert len(queued) == 1
    assert queued[0].wake_id == "wake-b"
    assert queued[0].held_sell_reauction_requests == (fresh,)

    assert reactor_wake.persist_held_sell_reauction_receipts(
        (_v4_actuated_receipt(fresh),),
        path=path,
    )
    assert reactor_wake.acknowledge_reactor_wake(queued[0], path=path)
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=path) == frozenset()
    assert reactor_wake.read_reactor_wake(path=path) is None


def test_held_sell_completion_fails_bounded_when_lineage_fence_is_stalled(
    tmp_path,
):
    from src.events import reactor
    from src.runtime import reactor_wake

    _path, _old, fresh = _v4_stable_debt_attempts(
        tmp_path,
        publish_old=False,
        publish_fresh=False,
    )
    path = tmp_path / "stalled-lineage-wake.json"
    assert reactor_wake._HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.acquire(
        timeout=1.0
    )
    started = time.monotonic()
    try:
        accepted = reactor.request_global_auction_completion(
            reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
            position_id=fresh.position_id,
            family=fresh.family,
            probability_content_identity=fresh.probability_content_identity,
            held_token_id=fresh.held_token_id,
            held_best_bid=fresh.held_best_bid,
            bid_observed_at=fresh.bid_observed_at,
            book_state=fresh.book_state,
            probability_observed_at=fresh.probability_observed_at,
            generation=fresh.generation,
            scope_identity=fresh.scope_identity,
            schema_version=fresh.schema_version,
            wake_path=path,
            force_new_generation=True,
        )
    finally:
        reactor_wake._HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.release()
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()

    assert accepted is False
    assert time.monotonic() - started < 0.75
    assert reactor_wake.reactor_wakes_since(None, path=path) == ()


def test_held_sell_lineage_flock_contention_has_one_total_deadline(
    monkeypatch,
    tmp_path,
):
    from src.runtime import reactor_wake

    real_flock = reactor_wake.fcntl.flock

    def contended_flock(descriptor, operation):
        if operation & reactor_wake.fcntl.LOCK_NB:
            raise BlockingIOError("held by another process")
        return real_flock(descriptor, operation)

    monkeypatch.setattr(reactor_wake.fcntl, "flock", contended_flock)
    started = time.monotonic()
    with pytest.raises(
        TimeoutError,
        match="HELD_SELL_REAUCTION_LINEAGE_LOCK_TIMEOUT",
    ):
        with reactor_wake._held_sell_reauction_lineage_lock(
            "stalled-cross-process-scope",
            path=tmp_path / "wake.json",
            timeout_seconds=0.03,
        ):
            pytest.fail("contended lineage fence must not admit the writer")

    assert time.monotonic() - started < 0.5
    assert not reactor_wake._HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.locked()


def test_v4_completion_lock_timeout_keeps_exact_wake_pending(tmp_path):
    from src.runtime import reactor_wake

    path, _old, fresh = _v4_stable_debt_attempts(tmp_path)
    assert reactor_wake._HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.acquire(
        timeout=1.0
    )
    started = time.monotonic()
    try:
        assert not reactor_wake.held_sell_reauction_requests_completed(
            (fresh,),
            path=path,
        )
    finally:
        reactor_wake._HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.release()

    assert time.monotonic() - started < 0.75
    queued = reactor_wake.reactor_wakes_since(None, path=path)
    assert len(queued) == 1
    assert queued[0].held_sell_reauction_requests == (fresh,)


def test_v4_queued_lookup_default_preserves_lineage_lock_call_shape(
    monkeypatch,
    tmp_path,
):
    from src.runtime import reactor_wake

    path, _old, fresh = _v4_stable_debt_attempts(tmp_path)
    real_lock = reactor_wake._held_sell_reauction_lineage_lock

    @contextmanager
    def legacy_lineage_lock(scope_identity, *, path=None):
        with real_lock(scope_identity, path=path):
            yield

    monkeypatch.setattr(
        reactor_wake,
        "_held_sell_reauction_lineage_lock",
        legacy_lineage_lock,
    )

    assert reactor_wake.v4_held_sell_reauction_request_is_queued(
        fresh,
        path=path,
    )


def test_v4_lineage_cleanup_closes_every_descriptor_after_unlock_error(
    monkeypatch,
    tmp_path,
):
    from src.runtime import reactor_wake

    real_flock = reactor_wake.fcntl.flock
    real_close = reactor_wake.os.close
    unlocked = 0
    closed = []

    def flaky_unlock(descriptor, operation):
        nonlocal unlocked
        if operation == reactor_wake.fcntl.LOCK_UN:
            unlocked += 1
            if unlocked == 1:
                raise OSError("first unlock failed")
        return real_flock(descriptor, operation)

    def observed_close(descriptor):
        closed.append(descriptor)
        return real_close(descriptor)

    monkeypatch.setattr(reactor_wake.fcntl, "flock", flaky_unlock)
    monkeypatch.setattr(reactor_wake.os, "close", observed_close)
    with pytest.raises(OSError, match="first unlock failed"):
        with reactor_wake._held_sell_reauction_lineage_locks(
            ("scope-a", "scope-b"),
            path=tmp_path / "wake.json",
        ):
            pass

    assert len(closed) == 2
    assert len(set(closed)) == 2
    assert not reactor_wake._HELD_SELL_REAUCTION_RECEIPT_LINEAGE_LOCK.locked()


def test_v4_lineage_read_error_fails_closed(tmp_path):
    from src.runtime import reactor_wake

    path, _old, fresh = _v4_stable_debt_attempts(tmp_path)
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (_v4_actuated_receipt(fresh),), path=path
    )

    lineage_path = reactor_wake._held_sell_reauction_lineage_path(
        fresh.scope_identity,
        path=path,
    )
    lineage_path.unlink()
    lineage_path.mkdir()
    assert not reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)


def test_v4_queue_read_error_fails_closed_without_publish(tmp_path):
    from src.events import reactor
    from src.runtime import reactor_wake

    path, old, _fresh = _v4_stable_debt_attempts(
        tmp_path,
        publish_fresh=False,
    )
    queue_path = reactor_wake._v4_wake_queue_target(
        old.scope_identity,
        path=path,
    )
    queue_path.unlink()
    queue_path.mkdir()

    accepted, request = reactor.request_global_auction_completion(
        reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
        position_id=old.position_id,
        family=old.family,
        probability_content_identity=old.probability_content_identity,
        held_token_id=old.held_token_id,
        held_best_bid=old.held_best_bid,
        bid_observed_at=old.bid_observed_at,
        book_state=old.book_state,
        probability_observed_at=old.probability_observed_at,
        schema_version=4,
        wake_path=path,
        return_request=True,
    )

    assert accepted is False
    assert request is None
    assert queue_path.is_dir()


def test_v4_latest_lookup_is_bounded_under_unrelated_backlog(monkeypatch, tmp_path):
    from src.events import reactor
    from src.runtime import reactor_wake

    path, _old, fresh = _v4_stable_debt_attempts(tmp_path)
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (_v4_actuated_receipt(fresh),),
        path=path,
    )
    for index in range(512):
        reactor_wake.publish_reactor_wake(
            source="bounded-backlog-antibody",
            reason="forecast_posterior_advanced",
            path=path,
            wake_id=f"unrelated-{index}",
            event_ids=(f"event-{index}",),
        )
    assert len(tuple(reactor_wake._wake_queue_dir(path).glob("*.json"))) == 513

    def unbounded_scan_forbidden(*_args, **_kwargs):
        raise AssertionError("V4 lookup must not scan the wake backlog")

    monkeypatch.setattr(reactor_wake, "_queued_wakes", unbounded_scan_forbidden)
    assert reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)
    accepted, request = reactor.request_global_auction_completion(
        reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
        position_id=fresh.position_id,
        family=fresh.family,
        probability_content_identity=fresh.probability_content_identity,
        held_token_id=fresh.held_token_id,
        held_best_bid=fresh.held_best_bid,
        bid_observed_at=fresh.bid_observed_at,
        book_state=fresh.book_state,
        probability_observed_at=fresh.probability_observed_at,
        schema_version=4,
        wake_path=path,
        return_request=True,
    )
    assert accepted is True
    assert request == fresh


def test_v4_receipt_lineage_restart_requires_and_retains_latest_attempt(tmp_path):
    from src.runtime import reactor_wake

    path, old, fresh = _v4_stable_debt_attempts(tmp_path)
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (_v4_actuated_receipt(old),), path=path
    )
    with reactor_wake._WAKE_QUEUE_CACHE_LOCK:
        reactor_wake._WAKE_QUEUE_CACHE.clear()
        reactor_wake._WAKE_QUEUE_REVISIONS.clear()
    assert not reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)

    fresh_receipt = _v4_actuated_receipt(fresh)
    assert reactor_wake.persist_held_sell_reauction_receipts((fresh_receipt,), path=path)
    with reactor_wake._WAKE_QUEUE_CACHE_LOCK:
        reactor_wake._WAKE_QUEUE_CACHE.clear()
        reactor_wake._WAKE_QUEUE_REVISIONS.clear()
    assert not reactor_wake.held_sell_reauction_requests_completed((old,), path=path)
    assert reactor_wake.held_sell_reauction_requests_completed((fresh,), path=path)
    assert reactor_wake._read_held_sell_reauction_receipt(
        fresh.request_id,
        path=path,
        attempt_identity=fresh.attempt_identity,
    ) == fresh_receipt


def test_v3_in_band_current_q_actuates_or_capital_rejects_only(monkeypatch):

    from src.events import reactor
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    request = make_held_sell_reauction_request(
        position_id="karachi-in-band",
        family=("Karachi", "2026-07-29", "low"),
        probability_content_identity="q-karachi-current",
        held_token_id="token-karachi-no",
        held_best_bid=0.17,
        bid_observed_at="2026-07-29T12:00:00+00:00",
        schema_version=3,
        book_state="EXECUTABLE",
        generation="karachi-current-generation",
    )
    monkeypatch.setattr(
        "src.engine.global_batch_runtime.held_sell_reauction_coverage",
        lambda **_kwargs: pytest.fail("receipt builder must not query global cache"),
    )
    actuated = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=_held_sell_completion_result(
            position_id=request.position_id,
            token_id=request.held_token_id,
            probability_content_identity="q-karachi-current",
        ),
    )
    assert actuated[0].status == "ACTUATED"
    assert actuated[0].schema_version == 3
    assert actuated[0].scope_identity == request.scope_identity
    assert actuated[0].answered_probability_content_identity == "q-karachi-current"

    rejected = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=_held_sell_completion_result(
            position_id=request.position_id,
            token_id=request.held_token_id,
            probability_content_identity="q-karachi-current",
            outcome="CAPITAL_REJECTED",
            terminal_no_trade_reason="GLOBAL_AUCTION_NO_TRADE:CASH_DOMINATES",
        ),
    )
    assert rejected[0].status == "CAPITAL_REJECTED"
    assert rejected[0].capital_objective_proof.endswith("CASH_DOMINATES")
    assert rejected[0].answered_probability_content_identity == "q-karachi-current"


def test_v3_excluded_or_unknown_q_global_cut_stays_pending(monkeypatch):

    from src.events import reactor
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    request = make_held_sell_reauction_request(
        position_id="karachi-no-book",
        family=("Karachi", "2026-07-29", "low"),
        probability_content_identity="",
        held_token_id="token-karachi-no-book",
        held_best_bid=None,
        bid_observed_at="",
        schema_version=3,
        book_state="UNKNOWN",
        generation="karachi-no-book-generation",
    )
    assert reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=_held_sell_completion_result(
            position_id=request.position_id,
            token_id=request.held_token_id,
            probability_content_identity="",
            outcome="INCOMPLETE",
        ),
    ) == ()


def test_v4_no_executable_book_cut_completes_only_exact_attempt(tmp_path):
    from src.events import reactor
    from src.runtime import reactor_wake

    common = {
        "position_id": "v4-no-book",
        "family": ("Karachi", "2026-08-09", "high"),
        "held_token_id": "token-v4-no-book",
        "schema_version": 4,
        "generation": "v4-no-book-generation",
    }
    no_book = reactor_wake.make_held_sell_reauction_request(
        **common,
        probability_content_identity="q-v4-no-book",
        probability_observed_at="2026-08-09T12:00:00+00:00",
        held_best_bid=0.0,
        bid_observed_at="2026-08-09T12:00:00+00:00",
        book_state="NO_EXECUTABLE_BOOK",
    )
    fresh_book = reactor_wake.make_held_sell_reauction_request(
        **common,
        scope_identity=no_book.scope_identity,
        probability_content_identity="q-v4-fresh-book",
        probability_observed_at="2026-08-09T12:01:00+00:00",
        held_best_bid=0.21,
        bid_observed_at="2026-08-09T12:01:00+00:00",
        book_state="EXECUTABLE",
    )
    coverage = SimpleNamespace(
        position_id=no_book.position_id,
        token_id=no_book.held_token_id,
        status="EXCLUDED",
        book_state="NO_EXECUTABLE_BOOK",
        probability_content_identity="q-cut-current",
        selection_epoch_identity="epoch-v4-no-book",
        sell_book_witness_identity="no-book-witness-v4",
    )
    result = ReactorResult(
        global_held_sell_completion_cuts=[
            GlobalHeldSellCompletionCut(
                holding_coverage=(coverage,),
                economic_cut_completed=False,
                outcome="INCOMPLETE",
            )
        ]
    )

    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(no_book,), result=result
    )
    assert len(receipts) == 1
    assert receipts[0].status == "NO_EXECUTABLE_BOOK"
    assert receipts[0].book_state == "NO_EXECUTABLE_BOOK"
    assert receipts[0].answered_probability_content_identity == "q-cut-current"

    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        held_sell_reauction_requests=(no_book,),
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(receipts, path=path)
    assert reactor_wake.held_sell_reauction_requests_completed(
        (no_book,), path=path
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (fresh_book,), path=path
    )


def test_v4_expired_held_sell_deadline_terminalizes_exact_attempt_without_venue(
    tmp_path,
):
    from src.engine import global_batch_runtime
    from src.events import reactor
    from src.runtime import reactor_wake

    decision_at = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    request = reactor_wake.make_held_sell_reauction_request(
        position_id="deadline-position",
        family=("Paris", "2026-08-13", "high"),
        probability_content_identity="q-deadline",
        held_token_id="deadline-token",
        held_best_bid=0.21,
        bid_observed_at=(decision_at - timedelta(seconds=31)).isoformat(),
        probability_observed_at=(decision_at - timedelta(seconds=31)).isoformat(),
        completion_deadline_at=(decision_at - timedelta(seconds=1)).isoformat(),
        schema_version=4,
        book_state="EXECUTABLE",
    )
    venue_calls = 0

    def actuate(*_args):
        nonlocal venue_calls
        venue_calls += 1
        raise AssertionError("expired attempt cannot reach venue")

    result = global_batch_runtime.process_current_global_batch(
        (),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda _event: {},
        prepare_event=lambda *_args: pytest.fail("expired before preparation"),
        actuate_winner=actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue_calls,
        current_execution=lambda *_args: None,
        current_time_provider=lambda: decision_at,
        held_sell_reauction_requests=(request,),
    )

    assert venue_calls == 0
    assert result.held_sell_completion_cut is not None
    assert result.held_sell_completion_cut.outcome == "DEADLINE_EXPIRED"
    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=ReactorResult(
            global_held_sell_completion_cuts=[result.held_sell_completion_cut]
        ),
    )
    assert len(receipts) == 1
    assert receipts[0].status == reactor_wake.DEADLINE_EXPIRED
    assert receipts[0].completion_deadline_at == request.completion_deadline_at

    path = tmp_path / "deadline-wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        held_sell_reauction_requests=(request,),
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(receipts, path=path)
    assert reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=path
    )


def test_v4_deadline_receipt_cannot_ack_another_attempt(tmp_path):
    from src.runtime import reactor_wake

    deadline = "2026-08-13T12:00:30+00:00"
    common = dict(
        position_id="deadline-fence-position",
        family=("Paris", "2026-08-13", "high"),
        held_token_id="deadline-fence-token",
        schema_version=4,
        generation="deadline-fence-generation",
        scope_identity="deadline-fence-scope",
        book_state="EXECUTABLE",
        completion_deadline_at=deadline,
    )
    old = reactor_wake.make_held_sell_reauction_request(
        **common,
        probability_content_identity="q-old",
        probability_observed_at="2026-08-13T12:00:00+00:00",
        held_best_bid=0.21,
        bid_observed_at="2026-08-13T12:00:00+00:00",
    )
    fresh = reactor_wake.make_held_sell_reauction_request(
        **common,
        probability_content_identity="q-fresh",
        probability_observed_at="2026-08-13T12:00:20+00:00",
        held_best_bid=0.17,
        bid_observed_at="2026-08-13T12:00:20+00:00",
    )
    path = tmp_path / "deadline-fence-wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        held_sell_reauction_requests=(fresh,),
    )
    stale_receipt = reactor_wake.HeldSellReauctionReceipt(
        request_id=old.request_id,
        material_identity=old.material_identity,
        generation=old.generation,
        attempt_identity=old.attempt_identity,
        completion_deadline_at=old.completion_deadline_at,
        schema_version=4,
        scope_identity=old.scope_identity,
        book_state="EXECUTABLE",
        status=reactor_wake.DEADLINE_EXPIRED,
        reason="HELD_SELL_ACTUATION_DEADLINE_EXPIRED",
    )

    assert reactor_wake.persist_held_sell_reauction_receipts(
        (stale_receipt,), path=path
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (fresh,), path=path
    )


def test_v4_earliest_deadline_does_not_terminalize_later_attempt():
    from src.engine import global_batch_runtime
    from src.events import reactor
    from src.runtime import reactor_wake

    decision_at = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    def request(position_id: str, deadline: datetime):
        return reactor_wake.make_held_sell_reauction_request(
            position_id=position_id,
            family=("Paris", "2026-08-13", "high"),
            probability_content_identity=f"q-{position_id}",
            held_token_id=f"token-{position_id}",
            held_best_bid=0.21,
            bid_observed_at=(decision_at - timedelta(seconds=1)).isoformat(),
            probability_observed_at=(
                decision_at - timedelta(seconds=1)
            ).isoformat(),
            completion_deadline_at=deadline.isoformat(),
            schema_version=4,
            book_state="EXECUTABLE",
        )

    expired = request("expired", decision_at)
    live = request("live", decision_at + timedelta(seconds=10))
    result = global_batch_runtime.process_current_global_batch(
        (),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda _event: {},
        prepare_event=lambda *_args: pytest.fail("expired before preparation"),
        actuate_winner=lambda *_args: pytest.fail("expired before venue"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_args: None,
        current_time_provider=lambda: decision_at,
        held_sell_reauction_requests=(expired, live),
    )
    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(expired, live),
        result=ReactorResult(
            global_held_sell_completion_cuts=[result.held_sell_completion_cut]
        ),
    )

    assert [receipt.request_id for receipt in receipts] == [expired.request_id]
    assert receipts[0].status == reactor_wake.DEADLINE_EXPIRED


@pytest.mark.parametrize("book_state", ("UNKNOWN", "STALE"))
def test_v4_unknown_or_stale_book_cut_cannot_complete_attempt(book_state):
    from src.events import reactor
    from src.runtime import reactor_wake

    request = reactor_wake.make_held_sell_reauction_request(
        position_id=f"v4-{book_state.lower()}",
        family=("Karachi", "2026-08-09", "high"),
        probability_content_identity="q-v4-current",
        held_token_id=f"token-v4-{book_state.lower()}",
        held_best_bid=None,
        bid_observed_at="",
        schema_version=4,
        book_state=book_state,
    )
    coverage = SimpleNamespace(
        position_id=request.position_id,
        token_id=request.held_token_id,
        status="EXCLUDED",
        book_state=book_state,
        probability_content_identity="q-v4-current",
        selection_epoch_identity="epoch-v4-incomplete",
        sell_book_witness_identity="",
    )
    result = ReactorResult(
        global_held_sell_completion_cuts=[
            GlobalHeldSellCompletionCut(
                holding_coverage=(coverage,),
                economic_cut_completed=False,
                outcome="INCOMPLETE",
            )
        ]
    )

    assert reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,), result=result
    ) == ()


def test_held_sell_other_winner_never_emits_actuated_receipt():
    from src.events import reactor
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    request = make_held_sell_reauction_request(
        position_id="held-target",
        family=("Paris", "2026-07-30", "low"),
        probability_content_identity="q-target",
        held_token_id="token-target",
        held_best_bid=0.18,
        bid_observed_at="2026-07-30T08:00:00+00:00",
        schema_version=3,
        book_state="EXECUTABLE",
    )
    target = SimpleNamespace(
        position_id=request.position_id,
        token_id=request.held_token_id,
        status="EVALUATED",
        candidate_id="candidate-target",
        probability_content_identity="q-target",
        selection_epoch_identity="epoch-other",
        sell_book_witness_identity="book-target",
    )
    other = SimpleNamespace(
        position_id="held-other",
        token_id="token-other",
        status="EVALUATED",
        candidate_id="candidate-other",
        probability_content_identity="q-other",
        selection_epoch_identity="epoch-other",
        sell_book_witness_identity="book-other",
    )
    result = ReactorResult(
        global_held_sell_completion_cuts=[
            GlobalHeldSellCompletionCut(
                holding_coverage=(target, other),
                economic_cut_completed=True,
                outcome="ACTUATED",
                selected_position_id=other.position_id,
                selected_token_id=other.token_id,
                selected_candidate_id=other.candidate_id,
            )
        ]
    )

    assert reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,), result=result
    ) == ()


def test_terminal_position_without_current_coverage_keeps_durable_wake_pending(tmp_path):
    """Exact-cut receipts cannot retire terminal-position stale debt."""
    from src.events import reactor
    from src.runtime import reactor_wake

    request = reactor_wake.make_held_sell_reauction_request(
        position_id="economically-closed-stale-debt",
        family=("Paris", "2026-07-30", "low"),
        probability_content_identity="q-terminal-debt",
        held_token_id="token-terminal-debt",
        held_best_bid=0.18,
        bid_observed_at="2026-07-30T08:00:00+00:00",
        schema_version=3,
        book_state="EXECUTABLE",
    )
    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        held_sell_reauction_requests=(request,),
    )

    assert reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=_held_sell_completion_result(
            position_id=request.position_id,
            token_id=request.held_token_id,
            probability_content_identity="q-terminal-debt",
            outcome="INCOMPLETE",
        ),
    ) == ()
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=path
    )


@pytest.mark.parametrize(
    ("terminal_outcome", "terminal_reason", "expected_status"),
    (
        ("ACTUATED", "", "ACTUATED"),
        (
            "CAPITAL_REJECTED",
            "GLOBAL_AUCTION_NO_TRADE:CASH_DOMINATES",
            "CAPITAL_REJECTED",
        ),
    ),
)
def test_held_sell_multiwinner_waits_for_its_own_submit_or_final_cash(
    terminal_outcome,
    terminal_reason,
    expected_status,
):
    from src.events import reactor
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    request = make_held_sell_reauction_request(
        position_id="held-multiwinner",
        family=("Paris", "2026-07-30", "low"),
        probability_content_identity="q-multiwinner",
        held_token_id="token-multiwinner",
        held_best_bid=0.18,
        bid_observed_at="2026-07-30T08:00:00+00:00",
        schema_version=3,
        book_state="EXECUTABLE",
    )
    first_other = GlobalHeldSellCompletionCut(
        holding_coverage=(
            SimpleNamespace(
                position_id=request.position_id,
                token_id=request.held_token_id,
                status="EVALUATED",
                candidate_id="candidate-held",
                probability_content_identity="q-multiwinner",
                selection_epoch_identity="epoch-one",
                sell_book_witness_identity="book-one",
            ),
            SimpleNamespace(
                position_id="held-other-multiwinner",
                token_id="token-other-multiwinner",
                status="EVALUATED",
                candidate_id="candidate-other-multiwinner",
                probability_content_identity="q-other",
                selection_epoch_identity="epoch-one",
                sell_book_witness_identity="book-other",
            ),
        ),
        economic_cut_completed=True,
        outcome="ACTUATED",
        selected_position_id="held-other-multiwinner",
        selected_token_id="token-other-multiwinner",
        selected_candidate_id="candidate-other-multiwinner",
    )
    terminal = _held_sell_completion_result(
        position_id=request.position_id,
        token_id=request.held_token_id,
        probability_content_identity="q-multiwinner",
        outcome=terminal_outcome,
        terminal_no_trade_reason=terminal_reason,
    ).global_held_sell_completion_cuts[0]

    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=ReactorResult(
            global_held_sell_completion_cuts=[first_other, terminal]
        ),
    )

    assert [receipt.status for receipt in receipts] == [expected_status]


def test_v3_old_generation_receipt_cannot_ack_new_generation(tmp_path):
    from src.runtime.reactor_wake import (
        HeldSellReauctionReceipt,
        held_sell_reauction_requests_completed,
        make_held_sell_reauction_request,
        persist_held_sell_reauction_receipts,
    )

    kwargs = dict(
        position_id="karachi-generation-fence",
        family=("Karachi", "2026-07-29", "high"),
        probability_content_identity="q-karachi-generation",
        held_token_id="token-karachi-generation",
        held_best_bid=0.22,
        bid_observed_at="2026-07-29T12:00:00+00:00",
        schema_version=3,
        book_state="EXECUTABLE",
    )
    old = make_held_sell_reauction_request(**kwargs, generation="old")
    new = make_held_sell_reauction_request(**kwargs, generation="new")
    assert persist_held_sell_reauction_receipts(
        (
            HeldSellReauctionReceipt(
                request_id=old.request_id,
                material_identity=old.material_identity,
                generation=old.generation,
                schema_version=3,
                scope_identity=old.scope_identity,
                book_state="EXECUTABLE",
                status="ACTUATED",
                reason="current_global_cut_actuated",
                selection_epoch_identity="epoch-old",
                sell_book_witness_identity="book-old",
                answered_probability_content_identity="q-karachi-generation",
                attempt_identity=old.attempt_identity,
            ),
        ),
        path=tmp_path / "wake.json",
    )
    assert not held_sell_reauction_requests_completed(
        (new,), path=tmp_path / "wake.json"
    )


def test_held_sell_reauction_request_round_trips_through_durable_wake(tmp_path):
    import json

    from src.runtime import reactor_wake

    request = reactor_wake.make_held_sell_reauction_request(
        position_id="position-durable",
        family=("Paris", "2026-07-28", "low"),
        probability_content_identity="q-content-durable",
        held_token_id="token-no-durable",
        held_best_bid=0.13,
        bid_observed_at="2026-07-28T08:00:00+00:00",
    )
    path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        held_sell_reauction_requests=(request,),
    )

    stored = reactor_wake.reactor_wakes_since(None, path=path)
    queue_file = next((tmp_path / "wake.json.d").glob("*.json"))
    payload = json.loads(queue_file.read_text(encoding="utf-8"))
    stored_request = payload["held_sell_reauction_requests"][0]

    assert len(stored) == 1
    assert stored[0].held_sell_reauction_requests == (request,)
    assert stored_request["material_identity"] == request.material_identity
    assert stored_request["generation"] == request.generation
    assert stored_request["request_id"] == request.request_id


def test_held_sell_reauction_current_coverage_emits_actuation_receipt(monkeypatch):

    from src.events import reactor
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    request = make_held_sell_reauction_request(
        position_id="position-covered",
        family=("Paris", "2026-07-28", "low"),
        probability_content_identity="q-content-covered",
        held_token_id="token-no-covered",
        held_best_bid=0.11,
        bid_observed_at="2026-07-28T08:00:00+00:00",
    )
    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=_held_sell_completion_result(
            position_id=request.position_id,
            token_id=request.held_token_id,
            probability_content_identity="q-content-covered",
        ),
    )

    assert len(receipts) == 1
    assert receipts[0].request_id == request.request_id
    assert receipts[0].material_identity == request.material_identity
    assert receipts[0].generation == request.generation
    assert receipts[0].status == "ACTUATED"
    assert receipts[0].selection_epoch_identity == "epoch:position-covered"
    assert receipts[0].sell_book_witness_identity == "book:position-covered"


def test_held_sell_reauction_global_no_trade_emits_typed_reject(monkeypatch):

    from src.events import reactor
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    request = make_held_sell_reauction_request(
        position_id="position-no-trade",
        family=("Paris", "2026-07-28", "low"),
        probability_content_identity="q-content-no-trade",
        held_token_id="token-no-no-trade",
        held_best_bid=0.11,
        bid_observed_at="2026-07-28T08:00:00+00:00",
    )
    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=_held_sell_completion_result(
            position_id=request.position_id,
            token_id=request.held_token_id,
            probability_content_identity="q-content-no-trade",
            outcome="CAPITAL_REJECTED",
            terminal_no_trade_reason="GLOBAL_AUCTION_NO_TRADE:CASH_DOMINATES",
        ),
    )

    assert len(receipts) == 1
    assert receipts[0].status == "REJECTED"
    assert receipts[0].reason.endswith("GLOBAL_AUCTION_NO_TRADE:CASH_DOMINATES")


def test_snapshot_reauction_forces_new_wake_generation(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from src.events import reactor
    from src.runtime import reactor_wake

    request_kwargs = {
        "position_id": "position-release-generation",
        "family": ("Paris", "2026-07-28", "low"),
        "probability_content_identity": "q-content-release-generation",
        "held_token_id": "token-no-release-generation",
        "held_best_bid": 0.10,
        "bid_observed_at": "2026-07-28T08:00:00+00:00",
        "schema_version": 3,
        "book_state": "EXECUTABLE",
    }
    old_request = reactor_wake.make_held_sell_reauction_request(
        **request_kwargs,
        generation="old-generation",
    )
    old_wake = SimpleNamespace(
        reason=reactor.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        forecast_families=(("Paris", "2026-07-28", "low"),),
        held_sell_reauction_requests=(old_request,),
    )
    published = []
    monkeypatch.setattr(
        reactor_wake,
        "reactor_wakes_since",
        lambda _at: (old_wake,),
    )
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **kwargs: published.append(kwargs) or SimpleNamespace(**kwargs),
    )

    assert reactor.request_global_auction_completion(
        reason="GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED",
        **request_kwargs,
        force_new_generation=True,
    ) is True
    assert len(published) == 1
    new_request = published[0]["held_sell_reauction_requests"][0]
    assert new_request.material_identity == old_request.material_identity
    assert new_request.generation != old_request.generation
    assert new_request.request_id != old_request.request_id

    receipt_path = tmp_path / "wake.json"
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (
            reactor_wake.HeldSellReauctionReceipt(
                request_id=old_request.request_id,
                material_identity=old_request.material_identity,
                generation=old_request.generation,
                schema_version=3,
                scope_identity=old_request.scope_identity,
                book_state="EXECUTABLE",
                status="ACTUATED",
                reason="GLOBAL_AUCTION_CURRENT_HOLDING_ACTUATED",
                selection_epoch_identity="epoch-old",
                sell_book_witness_identity="book-old",
                answered_probability_content_identity="q-content-release-generation",
                attempt_identity=old_request.attempt_identity,
            ),
        ),
        path=receipt_path,
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (new_request,),
        path=receipt_path,
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (
            reactor_wake.HeldSellReauctionReceipt(
                request_id=new_request.request_id,
                material_identity=new_request.material_identity,
                generation=new_request.generation,
                schema_version=3,
                scope_identity=new_request.scope_identity,
                book_state="EXECUTABLE",
                status="ACTUATED",
                reason="GLOBAL_AUCTION_CURRENT_HOLDING_ACTUATED",
                selection_epoch_identity="epoch-new",
                sell_book_witness_identity="book-new",
                answered_probability_content_identity="q-content-release-generation",
                attempt_identity=new_request.attempt_identity,
            ),
        ),
        path=receipt_path,
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (new_request,),
        path=receipt_path,
    )


def test_new_reauction_generation_survives_old_generation_ack(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    family = (("Paris", "2026-07-28", "low"),)
    old = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="old-in-flight-generation",
        forecast_families=family,
    )
    new = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        wake_id="new-release-generation",
        forecast_families=family,
    )

    assert reactor_wake.acknowledge_reactor_wake(old, path=path) is True
    remaining = reactor_wake.reactor_wakes_since(None, path=path)
    assert tuple(wake.wake_id for wake in remaining) == (new.wake_id,)


def test_day0_cancellation_does_not_discharge_monitor_fairness_debt():
    from types import SimpleNamespace

    from src.events import reactor

    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
    try:
        reactor._settle_global_auction_monitor_fairness(
            completion_due_at_start=True,
            result=SimpleNamespace(
                processed=0,
                proof_accepted=0,
                rejected=1,
                retried=1,
                global_auction_completed_non_cancelled=0,
                rejection_reasons=[
                    "GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED"
                ],
            ),
        )
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set() is True
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_new_fact_supersession_does_not_discharge_monitor_fairness_debt():
    from types import SimpleNamespace

    from src.events import reactor

    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
    try:
        reactor._settle_global_auction_monitor_fairness(
            completion_due_at_start=True,
            result=SimpleNamespace(
                processed=0,
                proof_accepted=0,
                rejected=1,
                retried=1,
                global_auction_completed_non_cancelled=0,
                rejection_reasons=[
                    "GLOBAL_AUCTION_SUPERSEDED_BY_NEW_FACT"
                ],
            ),
        )
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set() is True
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_completion_wake_recovers_debt_after_process_restart():
    from src.events import reactor

    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        due_at_start, cancellation_probe = (
            reactor._global_auction_monitor_cancellation_probe(
                None,
                completion_due=True,
            )
        )
        assert due_at_start is True
        assert cancellation_probe() is False
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set() is True
        assert (
            reactor._settle_global_auction_monitor_fairness(
                completion_due_at_start=True,
                result=ReactorResult(),
            )
            is False
        )
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set() is True
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_exact_v4_no_book_completion_discharges_monitor_fairness_debt():
    from types import SimpleNamespace

    from src.events import reactor

    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
    try:
        assert reactor._settle_global_auction_monitor_fairness(
            completion_due_at_start=True,
            result=SimpleNamespace(global_auction_completed_non_cancelled=0),
            terminal_no_book_completion=True,
        )
        assert not reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set()
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_pre_submit_rejection_cannot_discharge_monitor_fairness_debt():
    from types import SimpleNamespace

    from src.events import reactor

    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
    try:
        reactor._settle_global_auction_monitor_fairness(
            completion_due_at_start=True,
            result=SimpleNamespace(
                processed=0,
                proof_accepted=0,
                rejected=4,
                retried=2,
                global_auction_completed_non_cancelled=0,
                rejection_reasons=["EXECUTABLE_SNAPSHOT_STALE"],
            ),
        )
        assert reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set() is True
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


def test_monitor_handoff_defers_reactor_admission_only():
    import src.main as main

    was_pending = main._held_position_monitor_handoff_pending.is_set()
    was_bootstrap_complete = main._held_position_monitor_bootstrap_complete.is_set()
    try:
        main._held_position_monitor_handoff_pending.set()
        main._held_position_monitor_bootstrap_complete.set()
        assert main._defer_for_held_position_monitor("edli_event_reactor") is True
    finally:
        main._held_position_monitor_handoff_pending.clear()
        main._held_position_monitor_bootstrap_complete.clear()
        if was_pending:
            main._held_position_monitor_handoff_pending.set()
        if was_bootstrap_complete:
            main._held_position_monitor_bootstrap_complete.set()


@pytest.mark.parametrize(
    ("monitor_kwargs", "periodic_pending"),
    (
        ({}, True),
        ({"urgent_forecast": True}, False),
        ({"urgent_day0": True}, False),
    ),
)
def test_only_periodic_monitor_arms_global_auction_fairness(
    monkeypatch, monitor_kwargs, periodic_pending
):
    import src.main as main

    observed = []

    class ReactorGate:
        def acquire(self, *, timeout):
            observed.append(
                (
                    timeout,
                    main._held_position_monitor_handoff_pending.is_set(),
                    main._periodic_held_position_monitor_handoff_pending.is_set(),
                )
            )
            return False

    monkeypatch.setattr(main, "_edli_reactor_active_lock", ReactorGate())
    monkeypatch.setattr(main, "_day0_exit_monitor_priority_pending", lambda: False)
    main._held_position_monitor_handoff_pending.clear()
    main._periodic_held_position_monitor_handoff_pending.clear()

    assert main._exit_monitor_cycle(**monitor_kwargs) is False
    assert observed[0][1:] == (True, periodic_pending)
    assert main._held_position_monitor_handoff_pending.is_set() is False
    assert main._periodic_held_position_monitor_handoff_pending.is_set() is False


@pytest.mark.parametrize(
    "verdict",
    (
        "CASH_DOMINATES",
        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER",
        "ROBUST_MAJORITY_LOSS",
    ),
)
def test_global_batch_completed_economic_no_trade_consumes_current_epoch(verdict):
    conn, store = _store()
    events = (
        _forecast_event("global-no-trade-a", target_date="2026-05-25"),
        _forecast_event("global-no-trade-b", target_date="2026-05-25"),
    )
    for event in events:
        store.insert_or_ignore(event)
    reactor = _global_batch_probe_reactor(store, {})

    def _batch(events, _decision_time, *, claim_unpaged_winner=None):
        del claim_unpaged_winner
        return GlobalBatchSubmitResult(
            receipts={
                event.event_id: EventSubmissionReceipt(
                    submitted=False,
                    event_id=event.event_id,
                    causal_snapshot_id=event.causal_snapshot_id,
                    reason=f"GLOBAL_AUCTION_NO_TRADE:{verdict}",
                    proof_accepted=False,
                )
                for event in events
            },
            winner_event_id=None,
            venue_submit_count=0,
            economic_cut_completed=True,
        )

    reactor._submit.process_global_batch = _batch
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=2)

    assert result.retried == 0
    assert result.rejected == 2
    assert result.global_auction_completed_non_cancelled == 1
    assert all(_processing_status(conn, event.event_id) == "processed" for event in events)


@pytest.mark.parametrize(
    (
        "economic_cut_completed",
        "expected_status",
        "expected_retried",
        "expected_rejected",
    ),
    (
        (True, "processed", 0, 1),
        (False, "pending", 1, 0),
    ),
)
def test_global_batch_action_set_exhaustion_obeys_explicit_economic_cut(
    economic_cut_completed,
    expected_status,
    expected_retried,
    expected_rejected,
):
    conn, store = _store()
    event = _forecast_event("global-action-set-exhausted", target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = _global_batch_probe_reactor(store, {})

    def _batch(events, _decision_time, *, claim_unpaged_winner=None):
        del claim_unpaged_winner
        return GlobalBatchSubmitResult(
            receipts={
                item.event_id: EventSubmissionReceipt(
                    submitted=False,
                    event_id=item.event_id,
                    causal_snapshot_id=item.causal_snapshot_id,
                    reason=(
                        "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
                        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:"
                        "families=0:candidates=1"
                    ),
                    proof_accepted=False,
                )
                for item in events
            },
            winner_event_id=None,
            venue_submit_count=0,
            economic_cut_completed=economic_cut_completed,
        )

    reactor._submit.process_global_batch = _batch
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.retried == expected_retried
    assert result.rejected == expected_rejected
    assert result.global_auction_completed_non_cancelled == int(
        economic_cut_completed
    )
    assert _processing_status(conn, event.event_id) == expected_status
    if economic_cut_completed:
        terminal_reason = (
            "GLOBAL_AUCTION_NO_TRADE:"
            "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:"
            "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
            "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:"
            "families=0:candidates=1"
        )
        assert result.rejection_reasons == [terminal_reason]
        assert conn.execute(
            """
            SELECT stage, reason_code
            FROM decision_compile_failures
            WHERE event_id = ?
            """,
            (event.event_id,),
        ).fetchall() == [("EXECUTOR_EXPRESSIBILITY", terminal_reason)]
        assert conn.execute(
            """
            SELECT rejection_stage, rejection_reason, regret_bucket
            FROM no_trade_regret_events
            WHERE event_id = ?
            """,
            (event.event_id,),
        ).fetchall() == [
            ("EXECUTOR_EXPRESSIBILITY", terminal_reason, "HONEST_MARKET")
        ]
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM decision_compile_failures
            WHERE event_id = ? AND reason_code LIKE 'UNKNOWN_REVIEW_REQUIRED%'
            """,
            (event.event_id,),
        ).fetchone()[0] == 0


def test_cancelled_global_batch_is_not_a_fairness_completion():
    conn, store = _store()
    event = _forecast_event("global-cancelled", target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = _global_batch_probe_reactor(store, {})

    def _batch(events, _decision_time, *, claim_unpaged_winner=None):
        del claim_unpaged_winner
        return GlobalBatchSubmitResult(
            receipts={
                item.event_id: EventSubmissionReceipt(
                    submitted=False,
                    event_id=item.event_id,
                    causal_snapshot_id=item.causal_snapshot_id,
                    reason="GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED",
                    proof_accepted=False,
                )
                for item in events
            },
            winner_event_id=None,
            venue_submit_count=0,
        )

    reactor._submit.process_global_batch = _batch
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.global_auction_completed_non_cancelled == 0


def test_global_batch_targeted_wake_claims_only_committed_event():
    conn, store = _store()
    ordinary = _forecast_event("global-ordinary", target_date="2026-05-25")
    committed = _forecast_event("global-committed", target_date="2026-05-25")
    store.insert_or_ignore(ordinary)
    store.insert_or_ignore(committed)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    reactor.process_pending(
        decision_time=_DT_VENUE_OPEN,
        limit=12,
        targeted_event_ids=frozenset({committed.event_id}),
        targeted_only=True,
    )

    assert observations["batch_event_ids"] == (committed.event_id,)
    assert _processing_status(conn, ordinary.event_id) == "pending"


def test_global_batch_producer_bridge_claims_target_and_one_oldest_debt():
    import src.events.reactor as reactor_module

    conn, store = _store()
    oldest_debt = _forecast_event("global-oldest-debt", target_date="2026-05-25")
    newer_debt = _forecast_event("global-newer-debt", target_date="2026-05-25")
    committed = _forecast_event("global-bridge-target", target_date="2026-05-25")
    for event in (oldest_debt, newer_debt, committed):
        store.insert_or_ignore(event)
    conn.execute(
        "UPDATE opportunity_event_processing SET updated_at = ? WHERE event_id = ?",
        ("2026-05-24T08:00:00+00:00", oldest_debt.event_id),
    )
    conn.execute(
        "UPDATE opportunity_event_processing SET updated_at = ? WHERE event_id = ?",
        ("2026-05-24T09:00:00+00:00", newer_debt.event_id),
    )
    conn.execute(
        "UPDATE opportunity_event_processing SET updated_at = ? WHERE event_id = ?",
        ("2026-05-24T10:00:00+00:00", committed.event_id),
    )
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    reactor.process_pending(
        decision_time=_DT_VENUE_OPEN,
        limit=2,
        targeted_event_ids=frozenset({committed.event_id}),
        targeted_only=True,
        bridge_stale_debt_slots=1,
    )

    assert observations["batch_event_ids"] == (
        committed.event_id,
        oldest_debt.event_id,
    )
    assert _processing_status(conn, newer_debt.event_id) == "pending"
    source = inspect.getsource(reactor_module.run_edli_event_reactor_cycle)
    assert "bridge_stale_debt_slots=1 if targeted_only_fast_path else 0" in source
    process_source = inspect.getsource(OpportunityEventReactor.process_pending)
    assert 'fetch_kwargs["bridge_stale_debt_slots"] = 0' in process_source


@pytest.mark.parametrize("winner_finalized", (True, False))
@pytest.mark.parametrize("batch_economic_cut_completed", (False, True))
def test_global_batch_prioritizes_venue_side_effect_and_stops_repeated_waits(
    monkeypatch, winner_finalized, batch_economic_cut_completed
):
    conn, store = _store()
    events = tuple(
        _forecast_event(f"lock-priority-{index}", target_date="2026-05-25")
        for index in range(3)
    )
    for event in events:
        store.insert_or_ignore(event)
    reactor = _global_batch_probe_reactor(store, {})
    winner = events[-1]
    actuated_cut = _held_sell_completion_result(
        position_id="held-window-b-actuated",
        token_id="token-window-b-actuated",
        probability_content_identity="q-window-b-actuated",
    ).global_held_sell_completion_cuts[0]

    def _batch(events, _decision_time, *, claim_unpaged_winner=None):
        receipts = {
            event.event_id: EventSubmissionReceipt(
                submitted=event.event_id == winner.event_id,
                event_id=event.event_id,
                causal_snapshot_id=event.causal_snapshot_id,
                side_effect_status=(
                    "VENUE_SUBMIT_ACKED"
                    if event.event_id == winner.event_id
                    else "NO_SUBMIT"
                ),
                venue_call_started=event.event_id == winner.event_id,
                venue_ack_received=event.event_id == winner.event_id,
                reason="TEST_RECEIPT",
            )
            for event in events
        }
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=winner.event_id,
            venue_submit_count=1,
            # A valid successful-submit result is False.  True represents a
            # malformed legacy producer; either way the venue side effect and
            # exact held completion cut must remain authoritative.
            economic_cut_completed=batch_economic_cut_completed,
            held_sell_completion_cut=actuated_cut,
        )

    reactor._submit.process_global_batch = _batch
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "123")
    calls = []

    def _finalize(
        event,
        receipt,
        *,
        decision_time,
        result,
        wait_ms=None,
        continuation_event=None,
        claim_generation=None,
        claim_attempt_count=None,
    ):
        del (
            decision_time,
            continuation_event,
            claim_generation,
            claim_attempt_count,
        )
        calls.append(
            (
                event.event_id,
                receipt.side_effect_status,
                receipt.reason,
                wait_ms,
            )
        )
        if event.event_id == winner.event_id and winner_finalized:
            return True
        result.rejection_reasons.append("WORLD_WRITE_LOCK_BUSY_POST_SUBMIT")
        result.retried += 1
        return False

    reactor._finalize_deferred_event_unit = _finalize

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=3)

    assert result.global_auction_completed_non_cancelled == 0
    assert result.global_held_sell_completion_cuts[0].outcome == "ACTUATED"
    assert calls == (
        [
            (winner.event_id, "VENUE_SUBMIT_ACKED", "TEST_RECEIPT", None),
            (events[0].event_id, "NO_SUBMIT", "TEST_RECEIPT", 123),
        ]
        if winner_finalized
        else [
            (winner.event_id, "VENUE_SUBMIT_ACKED", "TEST_RECEIPT", None)
        ]
    )
    assert result.retried == (2 if winner_finalized else 3)
    assert result.rejection_reasons == [
        "WORLD_WRITE_LOCK_BUSY_POST_SUBMIT"
    ] * result.retried
    assert result.global_held_sell_completion_cuts == [actuated_cut]
    assert _processing_status(conn, events[1].event_id) == "processing"


def test_global_batch_rejects_partial_side_effect_as_completed_economic_cut():
    conn, store = _store()
    events = (
        _forecast_event("partial-side-effect-a", target_date="2026-05-25"),
        _forecast_event("partial-side-effect-b", target_date="2026-05-25"),
    )
    for event in events:
        store.insert_or_ignore(event)
    reactor = _global_batch_probe_reactor(store, {})
    observed = {}

    def _batch(claimed, _decision_time, *, claim_unpaged_winner=None):
        del claim_unpaged_winner
        return GlobalBatchSubmitResult(
            receipts={
                claimed[0].event_id: EventSubmissionReceipt(
                    submitted=False,
                    event_id=claimed[0].event_id,
                    causal_snapshot_id=claimed[0].causal_snapshot_id,
                    side_effect_status="POST_SUBMIT_UNKNOWN",
                    venue_call_started=True,
                    reason="SUBMIT_UNKNOWN_SIDE_EFFECT",
                    proof_accepted=True,
                ),
                claimed[1].event_id: EventSubmissionReceipt(
                    submitted=False,
                    event_id=claimed[1].event_id,
                    causal_snapshot_id=claimed[1].causal_snapshot_id,
                    reason=(
                        "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
                        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:"
                        "families=0:candidates=1"
                    ),
                    proof_accepted=False,
                ),
            },
            winner_event_id=None,
            venue_submit_count=0,
            economic_cut_completed=True,
        )

    def _finalize(
        event,
        receipt,
        *,
        decision_time,
        result,
        wait_ms=None,
        continuation_event=None,
        claim_generation=None,
        claim_attempt_count=None,
    ):
        del (
            decision_time,
            result,
            wait_ms,
            continuation_event,
            claim_generation,
            claim_attempt_count,
        )
        observed[event.event_id] = receipt.reason
        return True

    reactor._submit.process_global_batch = _batch
    reactor._finalize_deferred_event_unit = _finalize

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=2)

    assert result.global_auction_completed_non_cancelled == 0
    assert set(observed) == {event.event_id for event in events}
    assert set(observed.values()) == {
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        (
            "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
            "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:"
            "families=0:candidates=1"
        ),
    }


def test_global_batch_economic_cut_requires_durable_window_b_finalization(
    tmp_path,
):
    from src.events import reactor as reactor_module
    from src.runtime import reactor_wake

    conn, store = _store()
    event = _forecast_event(
        "economic-cut-window-b-lock",
        target_date="2026-05-25",
    )
    store.insert_or_ignore(event)
    reactor = _global_batch_probe_reactor(store, {})
    request = reactor_wake.make_held_sell_reauction_request(
        position_id="held-window-b-capital",
        family=("Paris", "2026-05-25", "high"),
        probability_content_identity="q-window-b-capital",
        held_token_id="token-window-b-capital",
        held_best_bid=0.18,
        bid_observed_at="2026-05-24T18:00:00+00:00",
        schema_version=3,
        book_state="EXECUTABLE",
    )
    wake_path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        held_sell_reauction_requests=(request,),
    )
    capital_cut = _held_sell_completion_result(
        position_id=request.position_id,
        token_id=request.held_token_id,
        probability_content_identity=request.probability_content_identity,
        outcome="CAPITAL_REJECTED",
        terminal_no_trade_reason="GLOBAL_AUCTION_NO_TRADE:CASH_DOMINATES",
    ).global_held_sell_completion_cuts[0]

    def _batch(claimed, _decision_time, *, claim_unpaged_winner=None):
        del claim_unpaged_winner
        return GlobalBatchSubmitResult(
            receipts={
                item.event_id: EventSubmissionReceipt(
                    submitted=False,
                    event_id=item.event_id,
                    causal_snapshot_id=item.causal_snapshot_id,
                    reason=(
                        "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
                        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:"
                        "families=0:candidates=1"
                    ),
                    proof_accepted=False,
                )
                for item in claimed
            },
            winner_event_id=None,
            venue_submit_count=0,
            economic_cut_completed=True,
            held_sell_completion_cut=capital_cut,
        )

    def _window_b_lock(
        claimed_event,
        _receipt,
        *,
        decision_time,
        result,
        wait_ms=None,
        continuation_event=None,
        claim_generation=None,
        claim_attempt_count=None,
    ):
        del (
            decision_time,
            wait_ms,
            continuation_event,
            claim_generation,
            claim_attempt_count,
        )
        reactor._store.requeue_pending(
            claimed_event.event_id,
            last_error="WORLD_WRITE_LOCK_BUSY_POST_SUBMIT",
        )
        result.retried += 1
        return False

    reactor._submit.process_global_batch = _batch
    reactor._finalize_deferred_event_unit = _window_b_lock

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.retried == 1
    assert result.global_auction_completed_non_cancelled == 0
    assert len(result.global_held_sell_completion_cuts) == 1
    incomplete_cut = result.global_held_sell_completion_cuts[0]
    assert incomplete_cut.outcome == "INCOMPLETE"
    assert incomplete_cut.economic_cut_completed is False
    assert reactor_module._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=result,
    ) == ()
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,),
        path=wake_path,
    )
    assert _processing_status(conn, event.event_id) == "pending"


def test_global_batch_incomplete_receipt_coverage_fails_closed_for_whole_epoch():
    conn, store = _store()
    events = (
        _forecast_event("incomplete-a", target_date="2026-05-25"),
        _forecast_event("incomplete-b", target_date="2026-05-25"),
    )
    for event in events:
        store.insert_or_ignore(event)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations, incomplete=True)

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=2)

    assert observations["batch_calls"] == 1
    assert observations["direct_submit_calls"] == 0
    assert result.proof_accepted == 0
    assert result.retried == 2
    assert all(_processing_status(conn, event.event_id) == "pending" for event in events)


def test_global_batch_materializes_unclaimed_winner_as_next_claim():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("claimed", target_date="2026-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="current-batch-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    observations = {}
    reactor = _global_batch_probe_reactor(
        store,
        observations,
        next_claim_event=target,
    )

    first = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert first.retried == 1
    assert _processing_status(conn, target.event_id) == "pending"
    row = conn.execute(
        "SELECT last_error FROM opportunity_event_processing WHERE event_id = ?",
        (target.event_id,),
    ).fetchone()
    assert row[0] == "GLOBAL_WINNER_TARGETED_CLAIM"
    assert store.fetch_pending(
        decision_time=_DT_VENUE_OPEN.isoformat(), limit=1
    )[0].event_id == target.event_id


def test_global_batch_claims_unpaged_winner_inside_same_epoch():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("same-epoch-owner", target_date="2099-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="same-epoch-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    def _same_epoch_batch(events, _decision_time, *, claim_unpaged_winner):
        assert tuple(event.event_id for event in events) == (claimed.event_id,)
        assert claim_unpaged_winner(target) == target
        assert _processing_status(conn, target.event_id) == "processing"
        receipts = {
            event.event_id: EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_RETRY",
                proof_accepted=False,
            )
            for event in (claimed, target)
        }
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=target.event_id,
            venue_submit_count=0,
        )

    reactor._submit.process_global_batch = _same_epoch_batch
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.retried == 2
    assert _processing_status(conn, claimed.event_id) == "pending"
    assert _processing_status(conn, target.event_id) == "pending"


def test_global_batch_claims_retained_causal_carrier_not_new_economic_id():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    source = _forecast_event("retained-causal-owner", target_date="2099-05-25")
    old_target = _next_claim_carrier(
        source,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="old-economic-identity",
        payload=json.loads(source.payload_json),
    )
    new_target = _next_claim_carrier(
        source,
        targeted_at=_DT_VENUE_OPEN + timedelta(seconds=30),
        economic_identity="new-economic-identity",
        payload=json.loads(source.payload_json),
    )
    store.insert_or_ignore(source)
    generation = "2026-05-25T06:10:00+00:00"
    assert store.claim(source.event_id, claimed_at=generation)
    assert store.prioritize_global_winner(
        old_target,
        current_batch_claim_generations={source.event_id: generation},
    )
    conn.commit()
    reactor = _global_batch_probe_reactor(store, {})

    claimed_event, claimed_at, lock_bounced = (
        reactor._claim_global_winner_for_actuation(
            new_target,
            current_batch_claim_generations={source.event_id: generation},
            result=ReactorResult(),
        )
    )

    assert claimed_event == old_target
    assert claimed_at is not None
    assert lock_bounced is False
    assert _processing_status(conn, old_target.event_id) == "processing"
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (new_target.event_id,),
    ).fetchone() is None


def test_global_batch_recovers_unpaged_claim_when_batch_raises():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("same-epoch-error", target_date="2099-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="same-epoch-error-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    def _raise_after_claim(events, _decision_time, *, claim_unpaged_winner):
        assert claim_unpaged_winner(target) == target
        raise RuntimeError("post-claim test failure")

    reactor._submit.process_global_batch = _raise_after_claim
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.retried == 2
    assert _processing_status(conn, claimed.event_id) == "pending"
    assert _processing_status(conn, target.event_id) == "pending"
    assert conn.execute(
        "SELECT COUNT(*) FROM opportunity_event_processing "
        "WHERE event_id IN (?, ?) AND processing_status='processing'",
        (claimed.event_id, target.event_id),
    ).fetchone()[0] == 0


def test_same_epoch_winner_claim_rejects_changed_batch_generation():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("same-epoch-race", target_date="2099-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="same-epoch-race-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    actual_generation = "2026-05-25T06:10:01+00:00"
    assert store.claim(claimed.event_id, claimed_at=actual_generation)
    conn.commit()
    reactor = _global_batch_probe_reactor(store, {})

    reactor_result = ReactorResult()
    claimed_event, result, lock_bounced = reactor._claim_global_winner_for_actuation(
        target,
        current_batch_claim_generations={
            claimed.event_id: "2026-05-25T06:10:00+00:00"
        },
        result=reactor_result,
    )

    assert result is None
    assert claimed_event is None
    assert lock_bounced is False
    assert reactor_result.claim_lock_bounces == 0
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() is None
    assert conn.execute(
        "SELECT claimed_at FROM opportunity_event_processing WHERE event_id = ?",
        (claimed.event_id,),
    ).fetchone()[0] == actual_generation


def test_global_winner_claim_mutex_busy_is_bounded(monkeypatch):
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("winner-mutex-busy", target_date="2099-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="winner-mutex-busy-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    actual_generation = "2026-05-25T06:10:00+00:00"
    assert store.claim(claimed.event_id, claimed_at=actual_generation)
    conn.commit()
    reactor = _global_batch_probe_reactor(store, {})
    waits: list[float] = []

    class _BusyMutex:
        def acquire(self, *, timeout):
            waits.append(timeout)
            return False

        def release(self):
            pytest.fail("an unacquired mutex must not be released")

    monkeypatch.setattr(
        "src.events.reactor.world_write_mutex",
        lambda: _BusyMutex(),
    )

    reactor_result = ReactorResult()
    claimed_event, result, lock_bounced = reactor._claim_global_winner_for_actuation(
        target,
        current_batch_claim_generations={claimed.event_id: actual_generation},
        result=reactor_result,
    )

    assert result is None
    assert claimed_event is None
    assert lock_bounced is True
    assert waits == [pytest.approx(0.75)]
    assert reactor_result.claim_lock_bounces == 1
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() is None


def test_global_claim_lock_bounce_retries_same_winner_before_reauction(
    tmp_path, monkeypatch, caplog
):
    """A bounced claim is queued, then the exact target is evaluated next cycle."""

    from src.engine.global_batch_runtime import _next_claim_carrier

    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    init_schema(conn)
    store = EventStore(conn)
    base = _forecast_event("global-composed-lock", target_date="2099-05-25")
    target = _next_claim_carrier(
        base,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="global-composed-lock-economic-identity",
        payload=json.loads(base.payload_json),
    )
    store.insert_or_ignore(base)
    conn.commit()
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "100")
    queue_calls = 0
    queue_waits = []
    real_queue = reactor._queue_global_winner_for_claim

    def _count_queue(*args, **kwargs):
        nonlocal queue_calls
        queue_calls += 1
        queue_waits.append(kwargs.get("wait_ms"))
        return real_queue(*args, **kwargs)

    reactor._queue_global_winner_for_claim = _count_queue
    batch_attempt = 0

    def _locked_then_recovered_batch(
        events, _decision_time, *, claim_unpaged_winner
    ):
        nonlocal batch_attempt
        batch_attempt += 1
        if batch_attempt == 1:
            blocker = sqlite3.connect(str(db_path), timeout=30.0)
            blocker.execute("PRAGMA busy_timeout = 30000")
            blocker.execute("BEGIN IMMEDIATE")
            try:
                claimed = claim_unpaged_winner(target)
            finally:
                blocker.rollback()
                blocker.close()
        else:
            assert tuple(event.event_id for event in events) == (target.event_id,)
            return GlobalBatchSubmitResult(
                receipts={
                    target.event_id: EventSubmissionReceipt(
                        False,
                        target.event_id,
                        target.causal_snapshot_id,
                        reason="TRADE_SCORE_NON_POSITIVE",
                        proof_accepted=False,
                    )
                },
                winner_event_id=None,
                venue_submit_count=0,
            )
        receipt_events = (*events, target) if claimed else events
        reason = "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM"
        return GlobalBatchSubmitResult(
            receipts={
                event.event_id: EventSubmissionReceipt(
                    False,
                    event.event_id,
                    event.causal_snapshot_id,
                    reason=reason,
                    proof_accepted=False,
                )
                for event in receipt_events
            },
            winner_event_id=target.event_id if claimed else None,
            venue_submit_count=0,
            next_claim_event=None if claimed else target,
        )

    reactor._submit.process_global_batch = _locked_then_recovered_batch

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="zeus.events.reactor"):
        first = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, f"composed global claim bounce waited too long: {elapsed:.3f}s"
    assert first.claim_lock_bounces == 1
    assert first.retried == 1
    assert queue_calls == 1
    assert queue_waits == [None]
    assert observations["direct_submit_calls"] == 0
    assert "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM" in TRANSIENT_MONEY_PATH_REASONS
    assert not any("UNKNOWN money-path reason" in row.message for row in caplog.records)
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    assert not conn.in_transaction
    assert _processing_status(conn, base.event_id) == "pending"
    assert conn.execute(
        "SELECT last_error FROM opportunity_event_processing WHERE event_id = ?",
        (base.event_id,),
    ).fetchone()[0] == "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM"
    assert _processing_status(conn, target.event_id) == "pending"
    assert conn.execute(
        "SELECT last_error FROM opportunity_event_processing WHERE event_id = ?",
        (target.event_id,),
    ).fetchone()[0] == "GLOBAL_WINNER_TARGETED_CLAIM"

    second = reactor.process_pending(
        decision_time=_DT_VENUE_OPEN + timedelta(minutes=1),
        limit=1,
    )

    assert batch_attempt == 2
    assert second.processed == 1
    assert second.rejected == 1
    assert second.proof_accepted == 0
    assert second.retried == 0
    assert _processing_status(conn, target.event_id) == "processed"


def test_global_batch_defers_target_when_claim_is_reclaimed_during_solve():
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    claimed = _forecast_event("claimed-then-reclaimed", target_date="2026-05-25")
    target = _next_claim_carrier(
        claimed,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="reclaimed-economic-identity",
        payload=json.loads(claimed.payload_json),
    )
    store.insert_or_ignore(claimed)
    observations = {}
    reactor = _global_batch_probe_reactor(
        store,
        observations,
        next_claim_event=target,
    )
    process_batch = reactor._submit.process_global_batch

    def _reclaim_then_solve(events, decision_time, **kwargs):
        assert store.claim(
            events[0].event_id,
            claimed_at="2026-05-25T06:16:00+00:00",
        )
        conn.commit()
        return process_batch(events, decision_time, **kwargs)

    reactor._submit.process_global_batch = _reclaim_then_solve

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.proof_accepted == 0
    assert result.retried == 1
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() is None
    assert conn.execute(
        "SELECT processing_status, claimed_at, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (claimed.event_id,),
    ).fetchone() == ("processing", "2026-05-25T06:16:00+00:00", None)


def test_global_target_keeps_claim_priority_after_transient_epoch():
    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    base = _forecast_event("target-retry", target_date="2026-05-25")
    target = _next_claim_carrier(
        base,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="test-economic-identity",
        payload=json.loads(base.payload_json),
    )
    assert store.prioritize_global_winner(target)
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert result.retried == 1
    assert result.rejection_reasons == [
        "SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_NO_CURRENT_WINNER"
    ]
    row = conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (target.event_id,),
    ).fetchone()
    assert tuple(row) == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")
    assert store.fetch_pending(
        decision_time=_DT_VENUE_OPEN.isoformat(), limit=1
    )[0].event_id == target.event_id


def test_global_target_is_visible_beyond_old_pending_active_scan_window():
    """A fresh targeted winner cannot starve behind more than 20k old rows."""

    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    base = _forecast_event("target-large-backlog", target_date="2026-05-25")
    target = _next_claim_carrier(
        base,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="large-backlog-economic-identity",
        payload=json.loads(base.payload_json),
    )
    assert store.prioritize_global_winner(target)

    target_row = tuple(
        conn.execute(
            "SELECT * FROM opportunity_events WHERE event_id = ?", (target.event_id,)
        ).fetchone()
    )
    event_rows = []
    processing_rows = []
    for index in range(20_002):
        event_id = f"old-backlog-{index:05d}"
        row = list(target_row)
        row[0] = event_id
        row[2] = f"Chicago|2026-05-25|high|old-{index:05d}"
        row[3] = f"old-backlog-source-{index:05d}"
        row[8] = f"old-payload-{index:05d}"
        row[9] = f"old-idempotency-{index:05d}"
        event_rows.append(tuple(row))
        processing_rows.append(
            (
                store.consumer_name,
                event_id,
                "pending",
                0,
                None,
                None,
                None,
                "2026-01-01T00:00:00+00:00",
            )
        )
    conn.executemany(
        "INSERT INTO opportunity_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        event_rows,
    )
    conn.executemany(
        "INSERT INTO opportunity_event_processing VALUES (?,?,?,?,?,?,?,?)",
        processing_rows,
    )
    conn.commit()

    fetched = store.fetch_pending(
        decision_time=_DT_VENUE_OPEN.isoformat(), limit=1
    )

    assert [event.event_id for event in fetched] == [target.event_id]


def test_latest_global_target_crosses_a_full_page_of_older_family_targets():
    """Only the latest global winner owns the next-claim lane across families."""

    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    older = []
    for index in range(13):
        base = _forecast_event(
            f"older-global-target-{index}",
            target_date=f"2026-06-{index + 1:02d}",
        )
        target = _next_claim_carrier(
            base,
            targeted_at=_DT_VENUE_OPEN + timedelta(seconds=index),
            economic_identity=f"older-economic-identity-{index}",
            payload=json.loads(base.payload_json),
        )
        assert store.prioritize_global_winner(target)
        older.append(target)

    base = _forecast_event("latest-global-target", target_date="2026-05-25")
    latest = _next_claim_carrier(
        base,
        targeted_at=_DT_VENUE_OPEN + timedelta(seconds=13),
        economic_identity="latest-economic-identity",
        payload=json.loads(base.payload_json),
    )
    assert store.prioritize_global_winner(latest)

    # Mirror the live ordering: finalization touches a full claimed page after
    # materializing the new target, making the older targets newer by updated_at.
    for index, target in enumerate(older[:12], start=1):
        conn.execute(
            "UPDATE opportunity_event_processing SET updated_at = ? WHERE event_id = ?",
            (
                (_DT_VENUE_OPEN + timedelta(minutes=1, seconds=index)).isoformat(),
                target.event_id,
            ),
        )

    fetched = store.fetch_pending(
        decision_time=(_DT_VENUE_OPEN + timedelta(minutes=2)).isoformat(),
        limit=12,
    )

    assert fetched[0].event_id == latest.event_id
    assert latest.event_id not in {event.event_id for event in fetched[1:]}


def test_global_target_does_not_preempt_stale_processing_recovery():
    conn, store = _store()
    stale = _day0_event("stale")
    target = _forecast_event("target", target_date="2026-05-24")
    store.insert_or_ignore(target)
    assert store.prioritize_global_winner(target)
    store.insert_or_ignore(stale)
    assert store.claim(
        stale.event_id,
        claimed_at="2026-05-24T18:00:00+00:00",
    )

    first = store.fetch_pending(
        decision_time=_DT_VENUE_OPEN.isoformat(),
        limit=1,
    )

    assert [event.event_id for event in first] == [stale.event_id]


def test_global_reactor_keeps_stale_day0_ahead_of_targeted_forecast():
    conn, store = _store()
    stale = _day0_event("stale-reactor")
    target = _forecast_event("target-reactor", target_date="2026-05-24")
    assert store.prioritize_global_winner(target)
    store.insert_or_ignore(stale)
    assert store.claim(
        stale.event_id,
        claimed_at="2026-05-24T18:00:00+00:00",
    )
    observations = {}
    reactor = _global_batch_probe_reactor(store, observations)

    reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert observations["batch_event_ids"] == (stale.event_id,)


def test_global_target_atomically_supersedes_only_older_pending_targets():
    conn, store = _store()
    old = _day0_event("old-target")
    new = _forecast_event("new-target", target_date="2026-05-24")
    unrelated = _forecast_event("unrelated", target_date="2026-05-24")
    assert store.prioritize_global_winner(old)
    store.insert_or_ignore(unrelated)

    assert store.prioritize_global_winner(new)

    states = {
        event_id: (status, reason)
        for event_id, status, reason in conn.execute(
            "SELECT event_id, processing_status, last_error "
            "FROM opportunity_event_processing"
        )
    }
    assert states[old.event_id] == (
        "expired",
        "GLOBAL_WINNER_TARGET_SUPERSEDED",
    )
    assert states[new.event_id] == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")
    assert states[unrelated.event_id] == ("pending", None)


def test_global_target_uses_dedicated_indexed_pointer():
    conn, store = _store()
    old = _day0_event("old-point-target")
    new = _forecast_event("new-point-target", target_date="2026-05-24")
    assert store.prioritize_global_winner(old)

    statements = []
    conn.set_trace_callback(statements.append)
    try:
        assert store.prioritize_global_winner(new)
    finally:
        conn.set_trace_callback(None)

    pointer_reads = [
        statement
        for statement in statements
        if "FROM opportunity_event_processing pointer" in statement
    ]
    assert pointer_reads
    assert all(
        "INDEXED BY idx_opportunity_event_processing_status" in statement
        for statement in pointer_reads
    )
    assert all(store._winner_pointer_consumer_name in statement for statement in pointer_reads)
    plan = " ".join(
        str(column)
        for row in conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT pointer.event_id
              FROM opportunity_event_processing pointer
                   INDEXED BY idx_opportunity_event_processing_status
             WHERE pointer.consumer_name = ?
               AND pointer.processing_status = 'pending'
             ORDER BY pointer.updated_at DESC
             LIMIT 2
            """,
            (store._winner_pointer_consumer_name,),
        )
        for column in row
    )
    assert "idx_opportunity_event_processing_status" in plan
    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (old.event_id,),
    ).fetchone() == ("expired", "GLOBAL_WINNER_TARGET_SUPERSEDED")
    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE consumer_name = ? AND event_id = ?",
        (store._winner_pointer_consumer_name, new.event_id),
    ).fetchone() == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")


def test_global_target_pointer_survives_restart_and_newer_backlog():
    import src.events.event_store as event_store

    conn, store = _store()
    target = _forecast_event("durable-pointer-target", target_date="2026-05-25")
    assert store.prioritize_global_winner(target)
    for index in range(600):
        ordinary = _forecast_event(
            f"newer-ordinary-{index}",
            target_date="2026-05-25",
        )
        store.insert_or_ignore(ordinary)
        conn.execute(
            "UPDATE opportunity_event_processing SET updated_at = ? "
            "WHERE consumer_name = ? AND event_id = ?",
            (
                (_DT_VENUE_OPEN + timedelta(seconds=index + 1)).isoformat(),
                store.consumer_name,
                ordinary.event_id,
            ),
        )
    event_store._winner_hints.clear()

    fetched = store.fetch_pending(
        decision_time=(_DT_VENUE_OPEN + timedelta(seconds=1)).isoformat(),
        limit=1,
    )

    assert [event.event_id for event in fetched] == [target.event_id]


def test_global_target_pointer_supersedes_after_restart_and_backlog():
    import src.events.event_store as event_store

    conn, store = _store()
    old = _forecast_event("restart-old-target", target_date="2026-05-24")
    new = _day0_event("restart-new-target")
    assert store.prioritize_global_winner(old)
    for index in range(600):
        store.insert_or_ignore(
            _forecast_event(
                f"restart-ordinary-{index}",
                target_date="2026-05-24",
            )
        )
    event_store._winner_hints.clear()

    assert store.prioritize_global_winner(new)

    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE consumer_name = ? AND event_id = ?",
        (store.consumer_name, old.event_id),
    ).fetchone() == ("expired", "GLOBAL_WINNER_TARGET_SUPERSEDED")
    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE consumer_name = ? AND event_id = ?",
        (store._winner_pointer_consumer_name, new.event_id),
    ).fetchone() == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")


def test_stale_winner_pointer_does_not_promote_ordinary_retry():
    import src.events.event_store as event_store

    conn, store = _store()
    target = _forecast_event("stale-pointer-target", target_date="2026-05-25")
    explicit = _forecast_event("explicit-target", target_date="2026-05-25")
    assert store.prioritize_global_winner(target)
    store.insert_or_ignore(explicit)
    store.requeue_pending(target.event_id, last_error="RETRY_OTHER_REASON")
    event_store._winner_hints.clear()

    fetched = store.fetch_pending(
        decision_time=(_DT_VENUE_OPEN + timedelta(seconds=1)).isoformat(),
        limit=2,
        targeted_event_ids=frozenset({explicit.event_id}),
        targeted_only=True,
    )

    assert [event.event_id for event in fetched] == [explicit.event_id]
    assert store._winner_hint() is None


def test_positive_global_winner_hint_survives_long_auction(monkeypatch):
    import src.events.event_store as event_store

    clock = [0.0]
    monkeypatch.setattr(event_store, "_monotonic", lambda: clock[0])
    conn, store = _store()
    target = _forecast_event("long-auction-target", target_date="2026-05-24")

    assert store.prioritize_global_winner(target)
    clock[0] = 10_000.0

    assert store._winner_hint() == (target.event_id, target.received_at)


def test_global_target_keeps_same_causal_fact_across_economic_epochs():
    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    source = _forecast_event("same-causal-fact", target_date="2026-05-24")
    first = _next_claim_carrier(
        source,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="book-epoch-1",
        payload=json.loads(source.payload_json),
    )
    second = _next_claim_carrier(
        source,
        targeted_at=_DT_VENUE_OPEN + timedelta(seconds=5),
        economic_identity="book-epoch-2",
        payload=json.loads(source.payload_json),
    )

    assert store.prioritize_global_winner(first)
    assert store.prioritize_global_winner(second)

    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (first.event_id,),
    ).fetchone() == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (second.event_id,),
    ).fetchone() is None


def test_superseded_global_target_gets_new_claim_carrier_identity():
    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    source = _forecast_event("superseded-causal-fact", target_date="2026-05-24")
    target = _next_claim_carrier(
        source,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="stable-economics",
        payload=json.loads(source.payload_json),
    )
    other_source = _forecast_event("other-causal-fact", target_date="2026-05-24")
    other = _next_claim_carrier(
        other_source,
        targeted_at=_DT_VENUE_OPEN + timedelta(seconds=1),
        economic_identity="other-economics",
        payload=json.loads(other_source.payload_json),
    )

    assert store.prioritize_global_winner(target)
    assert store.prioritize_global_winner(other)
    recovered = store.prioritized_global_winner_event(target)

    assert recovered is not None
    assert recovered.event_id != target.event_id
    assert recovered.source.startswith(f"{target.source}:claim_retry:")
    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() == ("expired", "GLOBAL_WINNER_TARGET_SUPERSEDED")
    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (recovered.event_id,),
    ).fetchone() == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")


@pytest.mark.parametrize("order_event_exists", (False, True))
def test_maintenance_expired_global_target_uses_aggregate_identity(
    order_event_exists,
):
    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    source = _forecast_event("maintenance-expired", target_date="2026-05-24")
    target = _next_claim_carrier(
        source,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="maintenance-expired-economics",
        payload=json.loads(source.payload_json),
    )
    store.insert_or_ignore(target)
    conn.execute(
        "UPDATE opportunity_event_processing "
        "SET processing_status='expired', last_error='GLOBAL_WINNER_TARGETED_CLAIM' "
        "WHERE consumer_name=? AND event_id=?",
        (store.consumer_name, target.event_id),
    )
    if order_event_exists:
        conn.execute(
            "INSERT INTO edli_live_order_events ("
            "aggregate_event_id,aggregate_id,event_sequence,event_type,"
            "event_hash,payload_json,payload_hash,source_authority,occurred_at,"
            "created_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (
                "maintenance-expired-order-event",
                f"{target.event_id}:final-intent",
                1,
                "DecisionProofAccepted",
                "maintenance-expired-event-hash",
                '{"event_id":"payload-must-not-own-event-identity"}',
                "maintenance-expired-payload-hash",
                "decision_kernel",
                _DT_VENUE_OPEN.isoformat(),
                _DT_VENUE_OPEN.isoformat(),
            ),
        )

    recovered = store.prioritized_global_winner_event(target)

    if order_event_exists:
        assert recovered is None
    else:
        assert recovered is not None
        assert recovered.event_id != target.event_id


@pytest.mark.parametrize("venue_attempted", (False, True))
@pytest.mark.parametrize(
    "reason",
    (
        (
            "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:"
            "EDLI_LIVE_QKERNEL_SELECTED_BOOK_CANDIDATE_REJECTED:"
            "QKERNEL_REST_THEN_CROSS_NOT_ACTIONABLE:policy=MAKER_TAKER_FORBIDDEN"
        ),
        (
            "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:"
            "PreSubmitRevalidated live order unit price out of bounds: "
            "live order unit price outside absolute inclusive [0.05, 0.95] "
            "submit band: price=0.036"
        ),
    ),
)
def test_global_target_recovers_processed_refreshable_no_submit_carrier(
    venue_attempted,
    reason,
):
    """A pre-venue book-race carrier may re-run only without a venue attempt."""

    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    source = _forecast_event("processed-mode-race", target_date="2026-05-25")
    target = _next_claim_carrier(
        source,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="stable-mode-race-economics",
        payload=json.loads(source.payload_json),
    )
    store.insert_or_ignore(target)
    assert store.claim(target.event_id, claimed_at=_DT_VENUE_OPEN.isoformat())
    store.mark_processed(target.event_id, processed_at=_DT_VENUE_OPEN.isoformat())
    conn.execute(
        "INSERT INTO no_trade_regret_events ("
        "regret_event_id,event_id,rejection_stage,rejection_reason,regret_bucket,"
        "created_at,schema_version) VALUES (?,?,?,?,?,?,1)",
        (
            "regret-processed-mode-race",
            target.event_id,
            "EXECUTOR_EXPRESSIBILITY",
            reason,
            "NO_SUBMIT",
            _DT_VENUE_OPEN.isoformat(),
        ),
    )
    if venue_attempted:
        payload_json = json.dumps(
            {"event_id": "payload-must-not-own-event-identity"},
            sort_keys=True,
            separators=(",", ":"),
        )
        conn.execute(
            "INSERT INTO edli_live_order_events ("
            "aggregate_event_id,aggregate_id,event_sequence,event_type,"
            "event_hash,payload_json,payload_hash,source_authority,occurred_at,"
            "created_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (
                "attempted-processed-mode-race",
                f"{target.event_id}:final-intent",
                1,
                "VenueSubmitAttempted",
                "event-hash",
                payload_json,
                "payload-hash",
                "engine_adapter",
                _DT_VENUE_OPEN.isoformat(),
                _DT_VENUE_OPEN.isoformat(),
            ),
        )

    assert store.prioritize_global_winner(target) is (not venue_attempted)
    assert conn.execute(
        "SELECT processing_status,claimed_at,processed_at,last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() == (
        (
            "pending",
            None,
            None,
            "GLOBAL_WINNER_TARGETED_CLAIM",
        )
        if not venue_attempted
        else (
            "processed",
            _DT_VENUE_OPEN.isoformat(),
            _DT_VENUE_OPEN.isoformat(),
            None,
        )
    )


def test_global_target_ignores_foreign_aggregate_payload_event_id():
    """Payload text cannot make another aggregate own this event."""

    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    source = _forecast_event("foreign-payload-event", target_date="2026-05-25")
    target = _next_claim_carrier(
        source,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="foreign-payload-economics",
        payload=json.loads(source.payload_json),
    )
    store.insert_or_ignore(target)
    assert store.claim(target.event_id, claimed_at=_DT_VENUE_OPEN.isoformat())
    store.mark_processed(target.event_id, processed_at=_DT_VENUE_OPEN.isoformat())
    conn.execute(
        "INSERT INTO no_trade_regret_events ("
        "regret_event_id,event_id,rejection_stage,rejection_reason,regret_bucket,"
        "created_at,schema_version) VALUES (?,?,?,?,?,?,1)",
        (
            "regret-foreign-payload-event",
            target.event_id,
            "EXECUTOR_EXPRESSIBILITY",
            (
                "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:"
                "EDLI_LIVE_QKERNEL_SELECTED_BOOK_CANDIDATE_REJECTED:"
                "QKERNEL_REST_THEN_CROSS_NOT_ACTIONABLE:"
                "policy=MAKER_TAKER_FORBIDDEN"
            ),
            "NO_SUBMIT",
            _DT_VENUE_OPEN.isoformat(),
        ),
    )
    payload_json = json.dumps(
        {"event_id": target.event_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    conn.execute(
        "INSERT INTO edli_live_order_events ("
        "aggregate_event_id,aggregate_id,event_sequence,event_type,"
        "event_hash,payload_json,payload_hash,source_authority,occurred_at,"
        "created_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
        (
            "foreign-aggregate-event",
            "different-event:final-intent",
            1,
            "VenueSubmitAttempted",
            "foreign-event-hash",
            payload_json,
            "foreign-payload-hash",
            "engine_adapter",
            _DT_VENUE_OPEN.isoformat(),
            _DT_VENUE_OPEN.isoformat(),
        ),
    )

    assert store.prioritize_global_winner(target)


def test_current_global_winner_recovers_old_causal_target_from_cross_family_starvation():
    conn, store = _store()
    from src.engine.global_batch_runtime import _next_claim_carrier

    old_source = _forecast_event("old-causal-target", target_date="2026-05-25")
    old_target = _next_claim_carrier(
        old_source,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="old-book-epoch",
        payload=json.loads(old_source.payload_json),
    )
    other_source = _forecast_event("newer-other-family", target_date="2026-05-26")
    other_payload = json.loads(other_source.payload_json)
    other_payload["city"] = "Seattle"
    other_target = _next_claim_carrier(
        other_source,
        targeted_at=_DT_VENUE_OPEN + timedelta(seconds=5),
        economic_identity="newer-other-book-epoch",
        payload=other_payload,
    )
    current_target = _next_claim_carrier(
        old_source,
        targeted_at=_DT_VENUE_OPEN + timedelta(seconds=10),
        economic_identity="current-book-epoch",
        payload=json.loads(old_source.payload_json),
    )

    assert store.prioritize_global_winner(old_target)
    assert store.prioritize_global_winner(other_target)
    # Recreate the legacy live shape: an older same-causal target survived next
    # to a newer cross-family target and therefore lost the sole fetch lane.
    conn.execute(
        "UPDATE opportunity_event_processing "
        "SET processing_status='pending', processed_at=NULL, "
        "last_error='GLOBAL_WINNER_TARGETED_CLAIM' WHERE event_id=?",
        (old_target.event_id,),
    )

    assert store.prioritize_global_winner(current_target)

    states = {
        event_id: (status, reason)
        for event_id, status, reason in conn.execute(
            "SELECT event_id, processing_status, last_error "
            "FROM opportunity_event_processing WHERE event_id IN (?, ?, ?)",
            (old_target.event_id, other_target.event_id, current_target.event_id),
        )
    }
    assert states[old_target.event_id] == (
        "pending",
        "GLOBAL_WINNER_TARGETED_CLAIM",
    )
    assert states[other_target.event_id] == (
        "expired",
        "GLOBAL_WINNER_TARGET_SUPERSEDED",
    )
    assert current_target.event_id not in states
    assert store.fetch_pending(
        decision_time=(_DT_VENUE_OPEN + timedelta(seconds=11)).isoformat(),
        limit=1,
    )[0].event_id == old_target.event_id


def test_global_target_processing_lease_blocks_new_target_materialization():
    conn, store = _store()
    inflight = _forecast_event("inflight-target", target_date="2026-05-25")
    new = _forecast_event("new-target", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:09:00+00:00",
    )

    assert store.prioritize_global_winner(new) is False

    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?",
        (inflight.event_id,),
    ).fetchone() == ("processing", None)
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (new.event_id,),
    ).fetchone() is None


def test_superseded_global_target_without_venue_attempt_cannot_starve_redecision(
    monkeypatch,
):
    import src.events.event_store as event_store
    from src.engine.global_batch_runtime import _next_claim_carrier

    clock = ["2026-05-24T18:00:00+00:00"]
    monkeypatch.setattr(event_store, "_utc_now", lambda: clock[0])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn, processing_lease_seconds=10)
    source = _forecast_event("stale-target-source", target_date="2026-05-25")
    stale_target = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]),
        economic_identity="stale-target-economics",
        payload=json.loads(source.payload_json),
    )
    current_target = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=11),
        economic_identity="current-target-economics",
        payload=json.loads(source.payload_json),
    )
    other_source = _forecast_event("other-family-source", target_date="2026-05-25")
    other_payload = json.loads(other_source.payload_json)
    other_payload["city"] = "Seattle"
    other_target = _next_claim_carrier(
        other_source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=1),
        economic_identity="other-family-economics",
        payload=other_payload,
    )

    assert store.prioritize_global_winner(stale_target)
    assert store.claim(stale_target.event_id, claimed_at=clock[0])
    clock[0] = "2026-05-24T18:00:01+00:00"
    assert store.prioritize_global_winner(other_target)

    clock[0] = "2026-05-24T18:00:02+00:00"
    recovered = store.prioritized_global_winner_event(current_target)

    assert recovered == current_target
    assert tuple(
        conn.execute(
            "SELECT processing_status, claimed_at, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, stale_target.event_id),
        ).fetchone()
    ) == (
        "expired",
        None,
        "GLOBAL_WINNER_TARGET_SUPERSEDED",
    )
    assert tuple(
        conn.execute(
            "SELECT processing_status, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store._winner_pointer_consumer_name, current_target.event_id),
        ).fetchone()
    ) == ("pending", GLOBAL_WINNER_TARGETED_CLAIM)


@pytest.mark.parametrize("live_order_table_available", (False, True))
def test_pointer_supersession_expires_pending_retry_carrier_without_command(
    monkeypatch,
    live_order_table_available,
):
    import src.events.event_store as event_store
    from src.engine.global_batch_runtime import _next_claim_carrier

    clock = ["2026-05-24T18:00:00+00:00"]
    monkeypatch.setattr(event_store, "_utc_now", lambda: clock[0])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn, processing_lease_seconds=10)
    source = _forecast_event("pending-retry-source", target_date="2026-05-25")
    stale = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]),
        economic_identity="pending-retry-old-economics",
        payload=json.loads(source.payload_json),
    )
    other_source = _forecast_event("pending-retry-other", target_date="2026-05-25")
    other_payload = json.loads(other_source.payload_json)
    other_payload["city"] = "Seattle"
    replacement = _next_claim_carrier(
        other_source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=1),
        economic_identity="pending-retry-new-economics",
        payload=other_payload,
    )

    assert store.prioritize_global_winner(stale)
    assert store.claim(stale.event_id, claimed_at=clock[0])
    store.requeue_pending(
        stale.event_id,
        last_error="WORLD_WRITE_LOCK_BUSY_POST_SUBMIT",
    )
    if not live_order_table_available:
        conn.execute("DROP TABLE edli_live_order_events")
    clock[0] = "2026-05-24T18:00:01+00:00"
    assert store.prioritize_global_winner(replacement)

    assert tuple(
        conn.execute(
            "SELECT processing_status, claimed_at, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, stale.event_id),
        ).fetchone()
    ) == (
        "expired",
        None,
        "GLOBAL_WINNER_TARGET_SUPERSEDED",
    )
    if live_order_table_available:
        assert not store.claim(stale.event_id, claimed_at=clock[0])


def test_pointer_supersession_preserves_pending_retry_with_durable_command(
    monkeypatch,
):
    import src.events.event_store as event_store
    from src.engine.global_batch_runtime import _next_claim_carrier

    clock = ["2026-05-24T18:00:00+00:00"]
    monkeypatch.setattr(event_store, "_utc_now", lambda: clock[0])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn, processing_lease_seconds=10)
    source = _forecast_event("pending-command-source", target_date="2026-05-25")
    stale = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]),
        economic_identity="pending-command-old-economics",
        payload=json.loads(source.payload_json),
    )
    other_source = _forecast_event("pending-command-other", target_date="2026-05-25")
    other_payload = json.loads(other_source.payload_json)
    other_payload["city"] = "Seattle"
    replacement = _next_claim_carrier(
        other_source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=1),
        economic_identity="pending-command-new-economics",
        payload=other_payload,
    )

    assert store.prioritize_global_winner(stale)
    assert store.claim(stale.event_id, claimed_at=clock[0])
    conn.execute(
        "INSERT INTO edli_live_order_events ("
        "aggregate_event_id,aggregate_id,event_sequence,event_type,"
        "event_hash,payload_json,payload_hash,source_authority,occurred_at,"
        "created_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
        (
            "pending-command-event",
            f"{stale.event_id}:final-intent",
            1,
            "ExecutionCommandCreated",
            "pending-command-hash",
            "{}",
            "pending-command-payload-hash",
            "engine_adapter",
            clock[0],
            clock[0],
        ),
    )
    store.requeue_pending(
        stale.event_id,
        last_error="WORLD_WRITE_LOCK_BUSY_POST_SUBMIT",
    )
    clock[0] = "2026-05-24T18:00:01+00:00"
    assert store.prioritize_global_winner(replacement)

    assert tuple(
        conn.execute(
            "SELECT processing_status, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, stale.event_id),
        ).fetchone()
    ) == (
        "pending",
        "WORLD_WRITE_LOCK_BUSY_POST_SUBMIT",
    )


@pytest.mark.parametrize(
    "fence_event_type",
    ("ExecutionCommandCreated", "VenueSubmitAttempted"),
)
def test_superseded_global_target_with_command_fence_remains_recovery_owned(
    monkeypatch,
    fence_event_type,
):
    import src.events.event_store as event_store
    from src.engine.global_batch_runtime import _next_claim_carrier

    clock = ["2026-05-24T18:00:00+00:00"]
    monkeypatch.setattr(event_store, "_utc_now", lambda: clock[0])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn, processing_lease_seconds=10)
    source = _forecast_event("attempted-target-source", target_date="2026-05-25")
    attempted = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]),
        economic_identity="attempted-target-economics",
        payload=json.loads(source.payload_json),
    )
    current = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=20),
        economic_identity="current-target-economics",
        payload=json.loads(source.payload_json),
    )
    other_source = _forecast_event("attempted-other-source", target_date="2026-05-25")
    other_payload = json.loads(other_source.payload_json)
    other_payload["city"] = "Seattle"
    other = _next_claim_carrier(
        other_source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=1),
        economic_identity="attempted-other-economics",
        payload=other_payload,
    )

    assert store.prioritize_global_winner(attempted)
    assert store.claim(attempted.event_id, claimed_at=clock[0])
    conn.execute(
        "INSERT INTO edli_live_order_events ("
        "aggregate_event_id,aggregate_id,event_sequence,event_type,"
        "event_hash,payload_json,payload_hash,source_authority,occurred_at,"
        "created_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
        (
            "attempted-target-event",
            f"{attempted.event_id}:final-intent",
            1,
            fence_event_type,
            "attempted-target-hash",
            "{}",
            "attempted-target-payload-hash",
            "engine_adapter",
            clock[0],
            clock[0],
        ),
    )
    clock[0] = "2026-05-24T18:00:01+00:00"
    assert store.prioritize_global_winner(other)

    clock[0] = "2026-05-24T18:00:20+00:00"
    assert store.prioritized_global_winner_event(current) is None
    assert tuple(
        conn.execute(
            "SELECT processing_status, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, attempted.event_id),
        ).fetchone()
    ) == ("processing", GLOBAL_WINNER_TARGETED_CLAIM)
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (current.event_id,),
    ).fetchone() is None


def test_global_target_command_fence_rejects_superseded_claim(monkeypatch):
    import src.events.event_store as event_store
    from src.engine.event_reactor_adapter import (
        _LiveOpportunityAlreadyLocked,
        _fence_global_target_claim_before_command,
    )
    from src.engine.global_batch_runtime import _next_claim_carrier

    clock = ["2026-05-24T18:00:00+00:00"]
    monkeypatch.setattr(event_store, "_utc_now", lambda: clock[0])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn, processing_lease_seconds=10)
    source = _forecast_event("fenced-old-source", target_date="2026-05-25")
    old = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]),
        economic_identity="fenced-old-economics",
        payload=json.loads(source.payload_json),
    )
    other_source = _forecast_event("fenced-other-source", target_date="2026-05-25")
    other_payload = json.loads(other_source.payload_json)
    other_payload["city"] = "Seattle"
    other = _next_claim_carrier(
        other_source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=1),
        economic_identity="fenced-other-economics",
        payload=other_payload,
    )

    assert store.prioritize_global_winner(old)
    assert store.claim(old.event_id, claimed_at=clock[0])
    old_attempt = store.attempt_count(old.event_id)
    clock[0] = "2026-05-24T18:00:01+00:00"
    assert store.prioritize_global_winner(other)

    with pytest.raises(
        _LiveOpportunityAlreadyLocked,
        match="GLOBAL_WINNER_CLAIM_FENCE_LOST",
    ):
        _fence_global_target_claim_before_command(
            conn,
            old,
            claimed_at="2026-05-24T18:00:00+00:00",
            attempt_count=old_attempt,
        )


def test_global_target_command_fence_serializes_before_pointer_supersession(
    monkeypatch,
):
    import src.events.event_store as event_store
    from src.engine.event_reactor_adapter import (
        _fence_global_target_claim_before_command,
    )
    from src.engine.global_batch_runtime import _next_claim_carrier

    clock = ["2026-05-24T18:00:00+00:00"]
    monkeypatch.setattr(event_store, "_utc_now", lambda: clock[0])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn, processing_lease_seconds=10)
    source = _forecast_event("fenced-command-source", target_date="2026-05-25")
    old = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]),
        economic_identity="fenced-command-economics",
        payload=json.loads(source.payload_json),
    )
    current = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=2),
        economic_identity="fenced-command-current-economics",
        payload=json.loads(source.payload_json),
    )
    other_source = _forecast_event("fenced-command-other", target_date="2026-05-25")
    other_payload = json.loads(other_source.payload_json)
    other_payload["city"] = "Seattle"
    other = _next_claim_carrier(
        other_source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=1),
        economic_identity="fenced-command-other-economics",
        payload=other_payload,
    )

    assert store.prioritize_global_winner(old)
    assert store.claim(old.event_id, claimed_at=clock[0])
    old_attempt = store.attempt_count(old.event_id)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    _fence_global_target_claim_before_command(
        conn,
        old,
        claimed_at=clock[0],
        attempt_count=old_attempt,
    )
    conn.execute(
        "INSERT INTO edli_live_order_events ("
        "aggregate_event_id,aggregate_id,event_sequence,event_type,"
        "event_hash,payload_json,payload_hash,source_authority,occurred_at,"
        "created_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
        (
            "fenced-command-event",
            f"{old.event_id}:final-intent",
            1,
            "ExecutionCommandCreated",
            "fenced-command-hash",
            "{}",
            "fenced-command-payload-hash",
            "engine_adapter",
            clock[0],
            clock[0],
        ),
    )
    conn.commit()

    clock[0] = "2026-05-24T18:00:01+00:00"
    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(other)
    conn.commit()

    assert tuple(
        conn.execute(
            "SELECT processing_status, claimed_at, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, old.event_id),
        ).fetchone()
    ) == ("processing", "2026-05-24T18:00:00+00:00", GLOBAL_WINNER_SUBMIT_FENCED)
    assert store.prioritized_global_winner_event(current) is None


def test_global_target_command_fence_rejects_reclaimed_old_generation(
    monkeypatch,
):
    import src.events.event_store as event_store
    from src.engine.event_reactor_adapter import (
        _LiveOpportunityAlreadyLocked,
        _fence_global_target_claim_before_command,
    )
    from src.engine.global_batch_runtime import _next_claim_carrier

    clock = ["2026-05-24T18:00:00+00:00"]
    monkeypatch.setattr(event_store, "_utc_now", lambda: clock[0])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn, processing_lease_seconds=10)
    source = _forecast_event("reclaimed-fence-source", target_date="2026-05-25")
    target = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]),
        economic_identity="reclaimed-fence-economics",
        payload=json.loads(source.payload_json),
    )

    assert store.prioritize_global_winner(target)
    first_generation = clock[0]
    assert store.claim(target.event_id, claimed_at=first_generation)
    first_attempt = store.attempt_count(target.event_id)
    clock[0] = "2026-05-24T18:00:11+00:00"
    second_generation = clock[0]
    assert store.claim(target.event_id, claimed_at=second_generation)
    second_attempt = store.attempt_count(target.event_id)

    with pytest.raises(
        _LiveOpportunityAlreadyLocked,
        match="GLOBAL_WINNER_CLAIM_FENCE_LOST",
    ):
        _fence_global_target_claim_before_command(
            conn,
            target,
            claimed_at=first_generation,
            attempt_count=first_attempt,
        )
    _fence_global_target_claim_before_command(
        conn,
        target,
        claimed_at=second_generation,
        attempt_count=second_attempt,
    )
    assert tuple(
        conn.execute(
            "SELECT claimed_at, attempt_count, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, target.event_id),
        ).fetchone()
    ) == (second_generation, second_attempt, GLOBAL_WINNER_SUBMIT_FENCED)
    clock[0] = "2026-05-24T18:00:22+00:00"
    assert not store.claim(target.event_id, claimed_at=clock[0])


def test_stale_global_target_with_venue_attempt_cannot_reclaim_or_refence(
    monkeypatch,
):
    import src.events.event_store as event_store
    from src.engine.event_reactor_adapter import (
        _LiveOpportunityAlreadyLocked,
        _fence_global_target_claim_before_command,
    )
    from src.engine.global_batch_runtime import _next_claim_carrier

    clock = ["2026-05-24T18:00:00+00:00"]
    monkeypatch.setattr(event_store, "_utc_now", lambda: clock[0])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn, processing_lease_seconds=10)
    source = _forecast_event("attempted-reclaim-source", target_date="2026-05-25")
    target = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]),
        economic_identity="attempted-reclaim-economics",
        payload=json.loads(source.payload_json),
    )

    assert store.prioritize_global_winner(target)
    first_generation = clock[0]
    assert store.claim(target.event_id, claimed_at=first_generation)
    first_attempt = store.attempt_count(target.event_id)
    conn.execute(
        "INSERT INTO edli_live_order_events ("
        "aggregate_event_id,aggregate_id,event_sequence,event_type,"
        "event_hash,payload_json,payload_hash,source_authority,occurred_at,"
        "created_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
        (
            "attempted-reclaim-event",
            f"{target.event_id}:final-intent",
            1,
            "VenueSubmitAttempted",
            "attempted-reclaim-hash",
            "{}",
            "attempted-reclaim-payload-hash",
            "engine_adapter",
            clock[0],
            clock[0],
        ),
    )
    clock[0] = "2026-05-24T18:01:11+00:00"

    assert store.fetch_pending(decision_time=clock[0], limit=1) == [target]
    assert not store.claim(target.event_id, claimed_at=clock[0])
    with pytest.raises(
        _LiveOpportunityAlreadyLocked,
        match="GLOBAL_WINNER_CLAIM_FENCE_LOST",
    ):
        _fence_global_target_claim_before_command(
            conn,
            target,
            claimed_at=first_generation,
            attempt_count=first_attempt,
        )
    assert tuple(
        conn.execute(
            "SELECT claimed_at, attempt_count, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, target.event_id),
        ).fetchone()
    ) == (first_generation, first_attempt, GLOBAL_WINNER_TARGETED_CLAIM)


def test_fenced_global_target_without_command_requeues_for_retry_and_boot():
    from src.engine.event_reactor_adapter import (
        _fence_global_target_claim_before_command,
    )
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    source = _forecast_event("fenced-no-command-source", target_date="2026-05-25")
    target = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat("2026-05-24T18:00:00+00:00"),
        economic_identity="fenced-no-command-economics",
        payload=json.loads(source.payload_json),
    )
    claimed_at = "2026-05-24T18:00:00+00:00"
    assert store.prioritize_global_winner(target)
    assert store.claim(target.event_id, claimed_at=claimed_at)
    attempt_count = store.attempt_count(target.event_id)
    _fence_global_target_claim_before_command(
        conn,
        target,
        claimed_at=claimed_at,
        attempt_count=attempt_count,
    )

    assert store.requeue_claim_if_current(
        target.event_id,
        claimed_at=claimed_at,
        attempt_count=attempt_count,
        last_error="GLOBAL_SELL_EXECUTION_FAILED",
    )
    assert tuple(
        conn.execute(
            "SELECT processing_status, claimed_at, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, target.event_id),
        ).fetchone()
    ) == ("pending", None, GLOBAL_WINNER_TARGETED_CLAIM)

    assert store.claim(
        target.event_id,
        claimed_at="2026-05-24T18:01:00+00:00",
    )
    second_attempt = store.attempt_count(target.event_id)
    _fence_global_target_claim_before_command(
        conn,
        target,
        claimed_at="2026-05-24T18:01:00+00:00",
        attempt_count=second_attempt,
    )
    assert (
        store.requeue_processing_before_boot(
            boot_at="2026-05-24T18:02:00+00:00"
        )
        == 1
    )
    assert tuple(
        conn.execute(
            "SELECT processing_status, claimed_at, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, target.event_id),
        ).fetchone()
    ) == ("pending", None, GLOBAL_WINNER_TARGETED_CLAIM)


@pytest.mark.parametrize("inject_venue_attempt_during_expiry", (False, True))
def test_legacy_orphaned_global_target_expiry_is_atomic_with_venue_attempt(
    monkeypatch,
    inject_venue_attempt_during_expiry,
):
    import src.events.event_store as event_store
    from src.engine.global_batch_runtime import _next_claim_carrier

    clock = ["2026-05-24T18:00:00+00:00"]
    monkeypatch.setattr(event_store, "_utc_now", lambda: clock[0])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn, processing_lease_seconds=10)
    source = _forecast_event("legacy-orphan-source", target_date="2026-05-25")
    orphan = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]),
        economic_identity="legacy-orphan-economics",
        payload=json.loads(source.payload_json),
    )
    current = _next_claim_carrier(
        source,
        targeted_at=datetime.fromisoformat(clock[0]) + timedelta(seconds=11),
        economic_identity="legacy-current-economics",
        payload=json.loads(source.payload_json),
    )

    assert store.prioritize_global_winner(orphan)
    assert store.claim(orphan.event_id, claimed_at=clock[0])
    conn.execute(
        "INSERT INTO edli_live_order_events ("
        "aggregate_event_id,aggregate_id,event_sequence,event_type,"
        "event_hash,payload_json,payload_hash,source_authority,occurred_at,"
        "created_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
        (
            "legacy-orphan-proof-event",
            f"{orphan.event_id}:final-intent",
            1,
            "DecisionProofAccepted",
            "legacy-orphan-proof-hash",
            "{}",
            "legacy-orphan-proof-payload-hash",
            "decision_kernel",
            clock[0],
            clock[0],
        ),
    )
    conn.execute(
        "UPDATE opportunity_event_processing "
        "SET processing_status='expired', processed_at=?, "
        "last_error='GLOBAL_WINNER_TARGET_SUPERSEDED', updated_at=? "
        "WHERE consumer_name=? AND event_id=?",
        (
            clock[0],
            clock[0],
            store._winner_pointer_consumer_name,
            orphan.event_id,
        ),
    )

    clock[0] = "2026-05-24T18:00:05+00:00"
    assert store.prioritized_global_winner_event(current) is None
    if inject_venue_attempt_during_expiry:
        original_table_exists = event_store._table_exists
        injected = False

        def _inject_attempt_after_ledger_check(connection, table):
            nonlocal injected
            exists = original_table_exists(connection, table)
            if table == "edli_live_order_events" and exists and not injected:
                injected = True
                connection.execute(
                    "INSERT INTO edli_live_order_events ("
                    "aggregate_event_id,aggregate_id,event_sequence,event_type,"
                    "event_hash,payload_json,payload_hash,source_authority,occurred_at,"
                    "created_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        "legacy-orphan-attempt-event",
                        f"{orphan.event_id}:final-intent",
                        2,
                        "VenueSubmitAttempted",
                        "legacy-orphan-attempt-hash",
                        "{}",
                        "legacy-orphan-attempt-payload-hash",
                        "engine_adapter",
                        clock[0],
                        clock[0],
                    ),
                )
            return exists

        monkeypatch.setattr(
            event_store,
            "_table_exists",
            _inject_attempt_after_ledger_check,
        )
    clock[0] = "2026-05-24T18:00:11+00:00"
    recovered = store.prioritized_global_winner_event(current)

    assert recovered == (None if inject_venue_attempt_during_expiry else current)
    assert tuple(
        conn.execute(
            "SELECT processing_status, last_error "
            "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
            (store.consumer_name, orphan.event_id),
        ).fetchone()
    ) == (
        (
            "processing",
            GLOBAL_WINNER_TARGETED_CLAIM,
        )
        if inject_venue_attempt_during_expiry
        else (
            "expired",
            "GLOBAL_WINNER_TARGET_SUPERSEDED",
        )
    )


def test_boot_recovers_targeted_claim_only_after_prior_owner_died():
    conn, store = _store()
    target = _forecast_event("boot-orphan-target", target_date="2026-05-25")
    assert store.prioritize_global_winner(target)
    assert store.claim(
        target.event_id,
        claimed_at="2026-05-24T18:00:00+00:00",
    )
    conn.execute(
        "UPDATE opportunity_event_processing "
        "SET processing_status='expired', processed_at=?, "
        "last_error='GLOBAL_WINNER_TARGET_SUPERSEDED' "
        "WHERE consumer_name=? AND event_id=?",
        (
            "2026-05-24T18:01:00+00:00",
            store._winner_pointer_consumer_name,
            target.event_id,
        ),
    )
    conn.commit()

    assert (
        store.requeue_processing_before_boot(
            boot_at="2026-05-24T18:05:00+00:00"
        )
        == 1
    )

    assert conn.execute(
        "SELECT processing_status, claimed_at, last_error "
        "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
        (store.consumer_name, target.event_id),
    ).fetchone() == ("pending", None, GLOBAL_WINNER_TARGETED_CLAIM)
    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
        (store._winner_pointer_consumer_name, target.event_id),
    ).fetchone() == ("pending", GLOBAL_WINNER_TARGETED_CLAIM)
    assert store.fetch_pending(
        decision_time="2026-05-24T18:06:00+00:00",
        limit=1,
    )[0].event_id == target.event_id


def test_late_targeted_requeue_cannot_replace_newer_winner_pointer():
    conn, store = _store()
    old = _forecast_event("late-old-target", target_date="2026-05-25")
    new = _forecast_event("late-new-target", target_date="2026-05-25")
    assert store.prioritize_global_winner(old)
    claimed_at = "2026-05-24T18:00:00+00:00"
    assert store.claim(old.event_id, claimed_at=claimed_at)
    attempt_count = store.attempt_count(old.event_id)
    conn.commit()

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        new,
        current_batch_claim_generations={old.event_id: claimed_at},
    )
    conn.commit()

    assert not store.requeue_claim_if_current(
        old.event_id,
        claimed_at=claimed_at,
        attempt_count=attempt_count,
        last_error=GLOBAL_WINNER_TARGETED_CLAIM,
    )

    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
        (store.consumer_name, old.event_id),
    ).fetchone() == ("expired", "GLOBAL_WINNER_TARGET_SUPERSEDED")
    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE consumer_name=? AND event_id=?",
        (store._winner_pointer_consumer_name, new.event_id),
    ).fetchone() == ("pending", GLOBAL_WINNER_TARGETED_CLAIM)


def test_boot_generation_requeues_only_prior_runtime_claims():
    conn, store = _store()
    old = _forecast_event("prior-runtime", target_date="2026-05-25")
    current = _forecast_event("current-runtime", target_date="2026-05-26")
    target = _forecast_event("prior-target", target_date="2026-05-27")
    for event in (old, current, target):
        store.insert_or_ignore(event)
    assert store.claim(old.event_id, claimed_at="2026-05-24T18:00:00+00:00")
    assert store.claim(current.event_id, claimed_at="2026-05-24T18:10:00+00:00")
    store.requeue_pending(target.event_id, last_error="GLOBAL_WINNER_TARGETED_CLAIM")
    assert store.claim(target.event_id, claimed_at="2026-05-24T18:01:00+00:00")

    assert (
        store.requeue_processing_before_boot(
            boot_at="2026-05-24T18:05:00+00:00"
        )
        == 2
    )

    states = {
        event_id: (status, claimed_at, reason)
        for event_id, status, claimed_at, reason in conn.execute(
            "SELECT event_id, processing_status, claimed_at, last_error "
            "FROM opportunity_event_processing"
        )
    }
    assert states[old.event_id] == ("pending", None, "PROCESS_OWNER_RESTARTED")
    assert states[target.event_id] == (
        "pending",
        None,
        "GLOBAL_WINNER_TARGETED_CLAIM",
    )
    assert states[current.event_id] == (
        "processing",
        "2026-05-24T18:10:00+00:00",
        None,
    )


def test_boot_backfills_legacy_winner_pointer_before_backlog_claim():
    import src.events.event_store as event_store
    from src.engine.global_batch_runtime import _next_claim_carrier

    conn, store = _store()
    old_source = _forecast_event("legacy-old-source", target_date="2026-05-25")
    new_source = _forecast_event("legacy-new-source", target_date="2026-05-25")
    old = _next_claim_carrier(
        old_source,
        targeted_at=_DT_VENUE_OPEN,
        economic_identity="legacy-old-economics",
        payload=json.loads(old_source.payload_json),
    )
    new = _next_claim_carrier(
        new_source,
        targeted_at=_DT_VENUE_OPEN + timedelta(seconds=1),
        economic_identity="legacy-new-economics",
        payload=json.loads(new_source.payload_json),
    )
    for target in (old, new):
        store.insert_or_ignore(target)
        conn.execute(
            "UPDATE opportunity_event_processing SET last_error = ? "
            "WHERE consumer_name = ? AND event_id = ?",
            (GLOBAL_WINNER_TARGETED_CLAIM, store.consumer_name, target.event_id),
        )
    for index in range(600):
        store.insert_or_ignore(
            _forecast_event(
                f"legacy-backlog-{index}",
                target_date="2026-05-25",
            )
        )
    event_store._winner_hints.clear()

    assert (
        store.requeue_processing_before_boot(
            boot_at=(_DT_VENUE_OPEN + timedelta(seconds=2)).isoformat()
        )
        == 0
    )

    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE consumer_name = ? AND event_id = ?",
        (store.consumer_name, old.event_id),
    ).fetchone() == ("expired", "GLOBAL_WINNER_TARGET_SUPERSEDED")
    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE consumer_name = ? AND event_id = ?",
        (store._winner_pointer_consumer_name, new.event_id),
    ).fetchone() == ("pending", GLOBAL_WINNER_TARGETED_CLAIM)
    fetched = store.fetch_pending(
        decision_time=(_DT_VENUE_OPEN + timedelta(seconds=3)).isoformat(),
        limit=1,
    )
    assert [event.event_id for event in fetched] == [new.event_id]


def test_boot_empty_winner_pointer_prevents_repeated_legacy_scan():
    conn, store = _store()
    from src.state.schema.opportunity_event_processing_schema import (
        assert_active_projection_ready,
    )

    importlib.import_module(
        "scripts.migrations.202608_edli_active_redecision_projection"
    ).up(conn)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        assert store.requeue_processing_before_boot(boot_at=_DT_VENUE_OPEN.isoformat()) == 0
        assert store.requeue_processing_before_boot(boot_at=_DT_VENUE_OPEN.isoformat()) == 0
    finally:
        conn.set_trace_callback(None)

    legacy_scans = [
        statement
        for statement in statements
        if "FROM opportunity_event_processing NOT INDEXED" in statement
    ]
    assert len(legacy_scans) == 1
    assert conn.execute(
        "SELECT processing_status, last_error "
        "FROM opportunity_event_processing WHERE consumer_name = ?",
        (store._winner_pointer_consumer_name,),
    ).fetchone() == ("pending", "GLOBAL_WINNER_POINTER_BOOTSTRAPPED_EMPTY")
    assert store._winner_pointer_consumer_name != "edli_reactor_v1"
    assert conn.execute(
        "SELECT COUNT(*) FROM opportunity_event_processing_type_projection"
    ).fetchone()[0] == 0
    assert assert_active_projection_ready(
        conn,
        consumer_name="edli_reactor_v1",
    ) == (0, 0)
    with pytest.raises(sqlite3.IntegrityError, match="ACTIVE_PROCESSING_REQUIRES_APPEND_ONLY_EVENT"):
        conn.execute(
            "INSERT INTO opportunity_event_processing "
            "(consumer_name, event_id, processing_status, updated_at) "
            "VALUES ('edli_reactor_v1', 'orphan-active', 'pending', ?)",
            (_DT_VENUE_OPEN.isoformat(),),
        )


def test_global_target_allows_only_current_batch_processing_lease():
    conn, store = _store()
    inflight = _forecast_event("inflight-current-batch", target_date="2026-05-25")
    new = _forecast_event("new-current-batch", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:09:00+00:00",
    )
    conn.commit()
    generations = {inflight.event_id: "2026-05-24T18:09:00+00:00"}

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        new,
        current_batch_claim_generations=generations,
    )
    conn.commit()
    states = {
        event_id: (status, reason)
        for event_id, status, reason in conn.execute(
            "SELECT event_id, processing_status, last_error "
            "FROM opportunity_event_processing"
        )
    }
    assert states[inflight.event_id][0] == "processing"
    assert states[new.event_id] == ("pending", "GLOBAL_WINNER_TARGETED_CLAIM")


def test_global_target_rejects_unowned_processing_lease_beside_current_batch():
    conn, store = _store()
    owned = _forecast_event("owned-current-batch", target_date="2026-05-25")
    external = _forecast_event("external-worker", target_date="2026-05-25")
    new = _forecast_event("new-mixed-lease", target_date="2026-05-25")
    for event in (owned, external):
        store.insert_or_ignore(event)
        assert store.claim(
            event.event_id,
            claimed_at="2026-05-24T18:09:00+00:00",
        )
    conn.commit()
    owned_generation = {owned.event_id: "2026-05-24T18:09:00+00:00"}

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        new,
        current_batch_claim_generations=owned_generation,
    ) is False
    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (new.event_id,),
    ).fetchone() is None


def test_global_target_rejects_stale_claim_generation_after_aba_reclaim():
    conn, store = _store()
    inflight = _forecast_event("inflight-aba", target_date="2026-05-25")
    new = _forecast_event("new-aba", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:00:00+00:00",
    )
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:06:00+00:00",
    )
    conn.commit()

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        new,
        current_batch_claim_generations={
            inflight.event_id: "2026-05-24T18:00:00+00:00"
        },
    ) is False
    conn.rollback()
    assert conn.execute(
        "SELECT claimed_at FROM opportunity_event_processing WHERE event_id = ?",
        (inflight.event_id,),
    ).fetchone()[0] == "2026-05-24T18:06:00+00:00"
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (new.event_id,),
    ).fetchone() is None


def test_global_target_commit_before_finalize_is_side_effect_free_and_reclaimable():
    conn, store = _store()
    inflight = _forecast_event("inflight-crash", target_date="2026-05-25")
    target = _forecast_event("target-after-crash", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-25T06:09:00+00:00",
    )
    conn.commit()
    generations = {inflight.event_id: "2026-05-25T06:09:00+00:00"}
    no_submit = GlobalBatchSubmitResult(
        receipts={
            inflight.event_id: EventSubmissionReceipt(
                False,
                inflight.event_id,
                inflight.causal_snapshot_id,
                reason="GLOBAL_TARGET_HANDOFF",
                proof_accepted=False,
            )
        },
        winner_event_id=None,
        venue_submit_count=0,
        next_claim_event=target,
    )

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        target,
        current_batch_claim_generations=generations,
    )
    conn.commit()
    fetched = store.fetch_pending(
        decision_time="2026-05-25T06:10:00+00:00",
        limit=1,
    )

    assert no_submit.venue_submit_count == 0
    assert not any(receipt.submitted for receipt in no_submit.receipts.values())
    assert _processing_status(conn, inflight.event_id) == "processing"
    assert [event.event_id for event in fetched] == [target.event_id]


def test_global_target_rejects_capability_that_left_processing():
    conn, store = _store()
    inflight = _forecast_event("inflight-disappeared", target_date="2026-05-25")
    target = _forecast_event("target-after-disappearance", target_date="2026-05-25")
    store.insert_or_ignore(inflight)
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:00:00+00:00",
    )
    old_capability = {inflight.event_id: "2026-05-24T18:00:00+00:00"}
    assert store.claim(
        inflight.event_id,
        claimed_at="2026-05-24T18:06:00+00:00",
    )
    store.requeue_pending(inflight.event_id, last_error="TRANSIENT_NEW_OWNER")
    conn.commit()

    conn.execute("BEGIN IMMEDIATE")
    assert store.prioritize_global_winner(
        target,
        current_batch_claim_generations=old_capability,
    ) is False
    conn.rollback()
    assert conn.execute(
        "SELECT 1 FROM opportunity_events WHERE event_id = ?",
        (target.event_id,),
    ).fetchone() is None


def test_global_batch_result_rejects_more_than_one_submit_or_wrong_winner():
    first = EventSubmissionReceipt(True, "first", side_effect_status="ACKED")
    second = EventSubmissionReceipt(True, "second", side_effect_status="ACKED")
    with pytest.raises(ValueError, match="at most one venue submit"):
        GlobalBatchSubmitResult(
            receipts={"first": first, "second": second},
            winner_event_id="first",
            venue_submit_count=2,
        )
    with pytest.raises(ValueError, match="submitted receipt must be the one global winner"):
        GlobalBatchSubmitResult(
            receipts={"first": first},
            winner_event_id=None,
            venue_submit_count=0,
        )
    target = _forecast_event("deferred-frontier", target_date="2026-05-25")
    continuation = _forecast_event(
        "submitted-continuation",
        target_date="2026-05-26",
    )
    with pytest.raises(ValueError, match="next global claim"):
        GlobalBatchSubmitResult(
            receipts={"first": first},
            winner_event_id="first",
            venue_submit_count=1,
            next_claim_event=target,
            continuation_event=continuation,
        )


def _retry_reactor(store, snapshot_present: dict):
    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: snapshot_present["v"],
        riskguard_gate=lambda _e: True,
        final_intent_submit=lambda _e, _dt: None,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )


def test_executable_snapshot_block_is_retryable_not_consumed_then_processes_after_capture():
    """A snapshot-block is TRANSIENT: the event is requeued (stays 'pending') rather than
    marked processed, so once the family's snapshots are captured a later cycle re-evaluates
    it instead of losing it. This is the #42b fix for the live reactor never running the kernel.
    """
    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    present = {"v": False}
    reactor = _retry_reactor(store, present)
    dt = _DT_VENUE_OPEN

    def _status():
        return conn.execute(
            "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]

    # 12 timely retries (well past the old cap of 8): the event requeues, never
    # consumed by an attempt count (operator law 2026-06-12: no caps). Snapshot
    # blocks use a retry floor, so advance to the stored not_before each cycle.
    for _ in range(12):
        result = reactor.process_pending(decision_time=dt)
        assert result.processed == 0
        assert result.dead_lettered == 0
        assert result.retried == 1
        assert _status() == "pending"  # retryable, NOT consumed, NO cap
        retry_floor = conn.execute(
            "SELECT claimed_at FROM opportunity_event_processing WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]
        dt = datetime.fromisoformat(retry_floor).astimezone(timezone.utc) + timedelta(seconds=1)

    present["v"] = True
    result = reactor.process_pending(decision_time=dt)
    assert result.processed == 1
    assert _status() == "processed"


def test_executable_snapshot_block_terminalizes_at_timeliness_horizon():
    """REWRITTEN 2026-06-12 (operator law "no caps"): an uncapturable snapshot is
    NOT dead-lettered by attempt count. While the event is timely it requeues
    indefinitely; it terminalizes only when its EVENT HORIZON (timeliness floor)
    has passed — labeled MONEY_PATH_HORIZON_EXPIRED."""
    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    present = {"v": False}  # never captured
    reactor = _retry_reactor(store, present)
    dt = _DT_VENUE_OPEN  # venue-open (SETTLEMENT_DAY): requeues while still tradeable

    def _status():
        return conn.execute(
            "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]

    # Many timely cycles: never dead-letters by count.
    for _ in range(12):
        reactor.process_pending(decision_time=dt)
        assert _status() == "pending"

    # Market horizon passes (Chicago 2026-05-24: the F1 12:00-UTC venue close is the
    # earliest horizon; the local-day floor is 2026-05-25T05:00Z). Drive the requeue
    # disposition at a past time to assert the explicit horizon terminal (in
    # production the read floor + archive sweep also reclaim it).
    from src.events.reactor import ReactorResult

    reactor._transient_requeue_reasons[event.event_id] = "EXECUTABLE_SNAPSHOT_BLOCKED"
    horizon_past = datetime(2026, 5, 26, 6, 0, tzinfo=timezone.utc)
    res = ReactorResult()
    reactor._finalize_disposition(
        event,
        "RETRY_EXECUTABLE_SNAPSHOT_PENDING",
        decision_time=horizon_past,
        result=res,
    )
    assert res.dead_lettered == 1
    assert _status() == "dead_letter"
    row = conn.execute(
        "SELECT failure_stage FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row[0] == "MONEY_PATH_HORIZON_EXPIRED"


def test_source_captured_after_decision_time_is_retryable_not_consumed():
    """The forecast-source re-ingestion race (SOURCE_CAPTURED_AFTER_DECISION_TIME) is TRANSIENT:
    the event is requeued and retried next cycle (decision_time advances past the source's
    available time) rather than consumed at the money-path stage. Mirrors the snapshot retry.
    """
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            reason="LIVE_INFERENCE_INPUTS_MISSING:FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:SOURCE_CAPTURED_AFTER_DECISION_TIME",
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.retried == 1
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "pending"


def test_stale_executable_snapshot_receipt_is_retryable_not_consumed():
    """A selected executable price can expire between pre-submit identity gating and JIT scoring.
    That is a transient market-data freshness race, not a terminal trade-score failure.
    """
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            reason=(
                "EXECUTABLE_SNAPSHOT_STALE:"
                "freshness_deadline=2026-05-24T06:09:59+00:00:"
                "decision_time=2026-05-24T06:10:00+00:00"
            ),
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.rejected == 0
    assert result.retried == 1
    assert result.rejection_reasons == [
        "EXECUTABLE_SNAPSHOT_STALE:"
        "freshness_deadline=2026-05-24T06:09:59+00:00:"
        "decision_time=2026-05-24T06:10:00+00:00"
    ]
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "pending"


def test_sqlite_lock_during_live_certificate_build_is_retryable_not_consumed():
    """SQLite writer contention during live certificate construction is transient.

    The event must stay pending for the next cycle; non-lock certificate failures
    remain terminal through the existing rejection path.
    """
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=False,
            reason="EDLI_LIVE_CERTIFICATE_BUILD_FAILED:database is locked",
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.rejected == 0
    assert result.retried == 1
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    assert _processing_status(conn, event.event_id) == "pending"


def test_live_book_authority_gap_requeues_with_selected_leg_identity():
    """A pre-submit book authority gap is a retryable execution-expression deferral.

    The adapter may have already selected a qkernel/Kelly leg before the final
    command certificate fails. The reactor must keep the event pending, while
    writing a token-bearing regret/deferral row so the price sidecar can pin and
    seed exactly that token before the next attempt.
    """
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="token-selected",
            outcome_label="YES",
            executable_snapshot_id="exec-selected",
            family_id="family-1",
            bin_label="80F",
            direction="buy_yes",
            q_live=0.71,
            q_lcb_5pct=0.62,
            c_fee_adjusted=0.40,
            c_cost_95pct=0.42,
            p_fill_lcb=0.55,
            trade_score=0.22,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=3,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=4.0,
            kelly_cost_basis_id="cost-1",
            final_intent_id="intent-1",
            reason="EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_MISSING",
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.rejected == 0
    assert result.retried == 1
    row = conn.execute(
        """
        SELECT rejection_stage, rejection_reason, token_id, bin_label, direction
        FROM no_trade_regret_events
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert row == (
        "EXECUTOR_EXPRESSIBILITY",
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_MISSING",
        "token-selected",
        "80F",
        "buy_yes",
    )
    assert _processing_status(conn, event.event_id) == "pending"


def test_sqlite_lock_during_post_submit_begin_is_retryable_not_dead_lettered(
    tmp_path, monkeypatch
):
    """A Window-B BEGIN IMMEDIATE lock is transient and cannot write evidence.

    The event is already claimed/committed as ``processing`` after Window A.
    When another writer holds the WAL write lock before Window B starts, the
    reactor must not try to write dead-letter/ledger rows through the same lock.
    Leaving the processing lease in place lets fetch_pending retry it once the
    lease is stale.
    """
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path, timeout=0)
    init_schema(conn)
    store = EventStore(conn)
    event = _forecast_event()
    store.insert_or_ignore(event)
    locker_holder: dict[str, sqlite3.Connection] = {}
    payload = json.loads(event.payload_json)
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "100")
    from src.events import reactor as reactor_module

    real_scoped_timeout = reactor_module._scoped_sqlite_busy_timeout
    observed_timeouts = []

    @contextmanager
    def _tracked_timeout(conn, timeout_ms):
        observed_timeouts.append(timeout_ms)
        with real_scoped_timeout(conn, timeout_ms):
            yield

    monkeypatch.setattr(reactor_module, "_scoped_sqlite_busy_timeout", _tracked_timeout)

    def _submit(_event, decision_time):
        locker = sqlite3.connect(db_path, timeout=0)
        locker.execute("BEGIN IMMEDIATE")
        locker_holder["conn"] = locker
        receipt = EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1",
            family_id="family-1",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id="intent-1",
        )
        return receipt

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    locker_holder["conn"].rollback()
    locker_holder["conn"].close()

    assert result.processed == 0
    assert result.dead_lettered == 0
    assert result.retried == 1
    assert result.rejection_reasons == ["WORLD_WRITE_LOCK_BUSY_POST_SUBMIT"]
    assert observed_timeouts[-2:] == [100, 0]
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    assert _processing_status(conn, event.event_id) == "processing"


def test_sqlite_lock_during_pre_submit_gate_is_retryable_not_dead_lettered():
    """A Window-A lock before any venue submit must leave the event retryable."""

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)

    def _locked_source_truth(_event):
        raise sqlite3.OperationalError("database is locked")

    def _submit(_event, _decision_time):
        raise AssertionError("pre-submit lock must not reach submit")

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=_locked_source_truth,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.dead_lettered == 0
    assert result.retried == 1
    assert result.rejection_reasons == ["WORLD_WRITE_LOCK_BUSY_PRE_SUBMIT"]
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    assert _processing_status(conn, event.event_id) == "pending"


def test_read_only_pre_submit_gates_run_without_world_writer():
    from src.state.db import world_mutex_is_held

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    lock_states: list[tuple[str, bool]] = []

    def _gate(name):
        def _check(*_args):
            lock_states.append((name, world_mutex_is_held()))
            return True

        return _check

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=_gate("source"),
        executable_snapshot_gate=_gate("snapshot"),
        riskguard_gate=_gate("risk"),
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda *_args: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert lock_states == [
        ("source", False),
        ("snapshot", False),
        ("risk", False),
    ]


def test_stale_unbound_executable_snapshot_receipt_is_retryable_not_consumed():
    """Stale JIT price failures may return before the adapter can build a bound final intent."""
    payload = json.loads(_forecast_event(target_date="2026-05-25").payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=False,
            event_id=event.event_id,
            causal_snapshot_id="stale-exec-failed-before-bound-proof",
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=True,
            reason=(
                "EXECUTABLE_SNAPSHOT_STALE:"
                "freshness_deadline=2026-05-24T06:09:59+00:00:"
                "decision_time=2026-05-24T06:10:00+00:00"
            ),
        )

    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.processed == 0
    assert result.rejected == 0
    assert result.retried == 1
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 0,
        "regret": 0,
        "dead_letter": 0,
    }
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "pending"










def test_duplicate_event_not_double_counted():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    store.insert_or_ignore(event)
    reactor, _rejected, submitted = _reactor(store)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert result.processed == 1


def test_reactor_persists_no_submit_certificate_before_processed():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.processed == 1
    cert_row = conn.execute(
        """
        SELECT certificate_hash, verifier_status
        FROM decision_certificates
        WHERE certificate_type = 'PreSubmitDecisionCertificate'
        """
    ).fetchone()
    assert cert_row is not None
    assert cert_row[1] == "VERIFIED"
    processing = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert processing[0] == "processed"
    assert len(_submitted) == 1


def test_source_truth_block_writes_decision_compile_failure():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store, gates=False)

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.rejected == 1
    failure = conn.execute(
        """
        SELECT stage, reason_code
        FROM decision_compile_failures
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert failure is not None
    assert failure[0] == "SOURCE_TRUTH"
    assert failure[1] == "SOURCE_TRUTH_BLOCKED"


def test_rejection_regret_uses_reactor_decision_time():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store, gates=False)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    reactor.process_pending(decision_time=decision_time)

    row = conn.execute(
        "SELECT decision_time FROM no_trade_regret_events WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == decision_time.isoformat()


def test_payload_decision_time_cannot_override_reactor_decision_time():
    conn, store = _store()
    event = _day0_event()
    payload = json.loads(event.payload_json)
    payload["decision_time"] = "2099-01-01T00:00:00+00:00"
    event = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store, gates=False)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    reactor.process_pending(decision_time=decision_time)

    row = conn.execute(
        "SELECT decision_time FROM no_trade_regret_events WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == decision_time.isoformat()
    assert row[0] != payload["decision_time"]


def test_all_candidates_rejected_regret_is_family_level_only():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    payload = json.loads(event.payload_json)
    payload.update(
        {
            "family_id": "family-chicago",
            "bin_label": "74F",
            "direction": "buy_yes",
            "condition_id": "condition-1",
            "token_id": "token-1",
            "q_live": 0.61,
            "q_lcb_5pct": 0.57,
            "c_fee_adjusted": 0.56,
            "trade_score": -0.01,
        }
    )
    event = replace(
        event,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda _event, _stage, _reason: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor._write_regret(
        event,
        "TRADE_SCORE",
        "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22; best_rejected=73F buy_no",
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
    )

    row = conn.execute(
        """
        SELECT family_id, bin_label, direction, condition_id, token_id,
               q_live, q_lcb_5pct, c_fee_adjusted, trade_score
          FROM no_trade_regret_events
         WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "family-chicago"
    assert row[1:] == (None, None, None, None, None, None, None, None)


def test_all_candidates_rejected_writes_structured_candidate_rows_from_receipt_book():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    payload = json.loads(event.payload_json)
    payload.update(
        {
            "family_id": "family-shanghai",
            "city": "Shanghai",
            "target_date": "2026-06-25",
            "metric": "high",
        }
    )
    event = replace(
        event,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        city="Shanghai",
        target_date="2026-06-25",
        metric="high",
        family_id="family-shanghai",
        executable_snapshot_id="exec-1",
        opportunity_book={
            "candidates": [
                {
                    "candidate_id": "candidate-buy-yes",
                    "family_id": "family-shanghai",
                    "condition_id": "condition-25",
                    "token_id": "yes-token-25",
                    "direction": "buy_yes",
                    "bin_label": "Will the highest temperature in Shanghai be 25°C on June 25?",
                    "execution_price": 0.6712,
                    "q_posterior": 0.9720,
                    "q_lcb_5pct": 0.9616,
                    "c_cost_95pct": 0.6712,
                    "p_fill_lcb": 1.0,
                    "trade_score": 0.4327,
                    "native_quote_available": True,
                    "missing_reason": "OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED:position_id=held-1",
                },
                {
                    "candidate_id": "candidate-no-edge",
                    "family_id": "family-shanghai",
                    "condition_id": "condition-27",
                    "token_id": "yes-token-27",
                    "direction": "buy_yes",
                    "bin_label": "Will the highest temperature in Shanghai be 27°C on June 25?",
                    "execution_price": 0.90,
                    "q_posterior": 0.10,
                    "q_lcb_5pct": 0.08,
                    "c_cost_95pct": 0.90,
                    "p_fill_lcb": 1.0,
                    "trade_score": -0.82,
                    "native_quote_available": True,
                    "missing_reason": "TRADE_SCORE_NON_POSITIVE",
                },
            ]
        },
    )
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda _event, _stage, _reason: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor._write_regret(
        event,
        "TRADE_SCORE",
        "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22; best_rejected=25C buy_yes",
        receipt=receipt,
        decision_time=datetime(2026, 6, 25, 4, 19, tzinfo=timezone.utc),
    )

    rows = conn.execute(
        """
        SELECT rejection_reason, family_id, bin_label, direction, condition_id,
               token_id, q_live, q_lcb_5pct, c_fee_adjusted, p_fill_lcb,
               trade_score, native_quote_available, executable_snapshot_id
          FROM no_trade_regret_events
         WHERE event_id = ?
         ORDER BY rejection_reason
        """,
        (event.event_id,),
    ).fetchall()
    assert len(rows) == 2
    family_summary = next(
        row for row in rows if row[0].startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
    )
    candidate = next(row for row in rows if row[0].startswith("EVENT_BOUND_CANDIDATE_REJECTED:"))
    assert family_summary[0].startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
    assert family_summary[2:11] == (None, None, None, None, None, None, None, None, None)
    assert candidate[0].startswith(
        "EVENT_BOUND_CANDIDATE_REJECTED:OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED:"
    )
    assert candidate[1] == "family-shanghai"
    assert candidate[2] == "Will the highest temperature in Shanghai be 25°C on June 25?"
    assert candidate[3] == "buy_yes"
    assert candidate[4] == "condition-25"
    assert candidate[5] == "yes-token-25"
    assert candidate[6:11] == (0.972, 0.9616, 0.6712, 1.0, 0.4327)
    assert candidate[11] == 1
    assert candidate[12] == "exec-1"


def test_qkernel_no_trade_writes_structured_candidate_rows_from_receipt_book():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    payload = json.loads(event.payload_json)
    payload.update(
        {
            "family_id": "family-beijing",
            "city": "Beijing",
            "target_date": "2026-06-26",
            "metric": "high",
        }
    )
    event = replace(
        event,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        city="Beijing",
        target_date="2026-06-26",
        metric="high",
        family_id="family-beijing",
        executable_snapshot_id="exec-qkernel",
        opportunity_book={
            "candidates": [
                {
                    "candidate_id": "candidate-buy-no-33c",
                    "family_id": "family-beijing",
                    "condition_id": "condition-33",
                    "token_id": "no-token-33",
                    "direction": "buy_no",
                    "bin_label": "Will the highest temperature in Beijing be 33°C on June 26?",
                    "execution_price": 0.74962,
                    "q_posterior": 0.8054,
                    "q_lcb_5pct": 0.773718,
                    "c_cost_95pct": 0.74962,
                    "p_fill_lcb": 1.0,
                    "trade_score": 0.0084,
                    "native_quote_available": True,
                    "missing_reason": None,
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "candidate_id": "NO:bin-33:DIRECT_NO:bin-33@proof",
                        "route_id": "DIRECT_NO:bin-33@proof",
                        "payoff_q_point": 0.779,
                        "payoff_q_lcb": 0.748,
                        "edge_lcb": -0.00162,
                        "point_ev": 0.031,
                        "delta_u_at_min": -0.0004,
                        "optimal_stake_usd": "0",
                        "optimal_delta_u": 0.0,
                        "q_dot_payoff": 0.779,
                        "cost": 0.74962,
                    },
                }
            ]
        },
    )
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda _event, _stage, _reason: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor._write_regret(
        event,
        "TRADE_SCORE",
        "QKERNEL_SPINE_NO_TRADE:NO_POSITIVE_EDGE_CANDIDATE",
        receipt=receipt,
        decision_time=datetime(2026, 6, 25, 5, 24, tzinfo=timezone.utc),
    )

    rows = conn.execute(
        """
        SELECT rejection_reason, bin_label, direction, condition_id, token_id,
               q_live, q_lcb_5pct, c_fee_adjusted, c_cost_95pct, trade_score
          FROM no_trade_regret_events
         WHERE event_id = ?
         ORDER BY rejection_reason
        """,
        (event.event_id,),
    ).fetchall()
    assert len(rows) == 2
    family_summary = next(row for row in rows if row[0].startswith("QKERNEL_SPINE_NO_TRADE:"))
    candidate = next(row for row in rows if row[0].startswith("EVENT_BOUND_CANDIDATE_REJECTED:"))
    assert family_summary[0] == "QKERNEL_SPINE_NO_TRADE:NO_POSITIVE_EDGE_CANDIDATE"
    assert family_summary[1:] == (None, None, None, None, None, None, None, None, None)
    assert candidate[0].startswith(
        "EVENT_BOUND_CANDIDATE_REJECTED:QKERNEL_SPINE_NO_TRADE:NO_POSITIVE_EDGE_CANDIDATE:"
    )
    assert candidate[1] == "Will the highest temperature in Beijing be 33°C on June 26?"
    assert candidate[2] == "buy_no"
    assert candidate[3] == "condition-33"
    assert candidate[4] == "no-token-33"
    assert candidate[5:10] == (0.779, 0.748, 0.74962, 0.74962, -0.00162)


def test_reactor_rejects_no_submit_receipt_without_decision_proof_bundle():
    conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    rejected = []

    def _submit(submitted_event, _decision_time):
        payload = json.loads(submitted_event.payload_json)
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=submitted_event.event_id,
            causal_snapshot_id=submitted_event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id="intent-1",
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
    )

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.rejected == 1
    assert rejected[0][1] == "DECISION_CERTIFICATE"
    assert rejected[0][2] == "PRE_SUBMIT_PROOF_BUNDLE_REQUIRED"
    assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 0
    failure = conn.execute(
        "SELECT stage, reason_code FROM decision_compile_failures WHERE event_id = ?",
        (event.event_id,),
    ).fetchall()
    assert ("PRE_SUBMIT_COMPILER", "PRE_SUBMIT_PROOF_BUNDLE_REQUIRED") in failure


def test_transition_proof_bundle_builder_not_used_in_runtime_reactor():
    _conn, store = _store()
    reactor, _rejected, _submitted = _reactor(store)

    assert not hasattr(reactor, "_build_transition_proof_bundle")


def test_receipt_insert_failure_does_not_leave_verified_orphan_certificate_graph():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("projection insert failed")

    reactor._no_submit_receipt_ledger.insert_idempotent = _raise  # type: ignore[method-assign]

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.dead_lettered == 1
    assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 0
    failure = conn.execute(
        "SELECT reason_code FROM decision_compile_failures WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert failure is not None
    assert "projection insert failed" in failure[0]


def test_certificate_insert_failure_rolls_back_event_processing():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("certificate graph insert failed")

    reactor._decision_certificate_ledger.persist_all = _raise  # type: ignore[method-assign]

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.dead_lettered == 1
    assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM edli_no_submit_receipts").fetchone()[0] == 0
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "dead_letter"
    surfaces = _terminal_surfaces(conn, event.event_id)
    assert surfaces["verified_no_submit"] == 0
    assert surfaces["compile_failure"] == 1
    assert surfaces["regret"] == 1
    assert surfaces["dead_letter"] == 1


def test_successful_no_submit_receipt_is_persisted_before_processed():
    from src.analysis.event_opportunity_report import build_event_opportunity_report

    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.proof_accepted == 1
    receipt_row = conn.execute(
        """
        SELECT event_id, side_effect_status, receipt_json, receipt_hash,
               kelly_decision_id, risk_decision_id
        FROM edli_no_submit_receipts
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert receipt_row is not None
    assert receipt_row[0] == event.event_id
    assert receipt_row[1] == "NO_SUBMIT"
    assert '"proof_accepted":true' in receipt_row[2]
    assert len(receipt_row[3]) == 64
    assert receipt_row[4] == "kelly-1"
    assert receipt_row[5] == "risk-1"
    status = conn.execute(
        """
        SELECT processing_status
        FROM opportunity_event_processing
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()[0]
    assert status == "processed"
    report = build_event_opportunity_report(conn)
    assert report["accepted_no_submit_receipts"] == 1
    assert report["certificate_time_semantics"]["generated_no_submit_decisions"] == 1


def test_terminal_trade_score_no_submit_receipt_is_persisted_before_rejection():
    conn, store = _store()
    event = _forecast_event(target_date="2026-05-25")
    store.insert_or_ignore(event)
    payload = json.loads(event.payload_json)

    def _submit(event, _decision_time):
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1",
            family_id="family-1",
            bin_label="80F",
            direction="buy_yes",
            q_live=0.51,
            q_lcb_5pct=0.47,
            c_fee_adjusted=0.56,
            c_cost_95pct=0.56,
            p_fill_lcb=1.0,
            trade_score=-0.09,
            trade_score_positive=False,
            reason="TRADE_SCORE_NON_POSITIVE",
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN)

    assert result.rejected == 1
    assert _processing_status(conn, event.event_id) == "processed"
    receipt_row = conn.execute(
        """
        SELECT side_effect_status, receipt_json, trade_score
        FROM edli_no_submit_receipts
        WHERE event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert receipt_row is not None
    assert receipt_row[0] == "NO_SUBMIT"
    assert '"reason":"TRADE_SCORE_NON_POSITIVE"' in receipt_row[1]
    assert receipt_row[2] == -0.09
    assert _terminal_surfaces(conn, event.event_id) == {
        "verified_no_submit": 0,
        "execution_receipt": 0,
        "compile_failure": 1,
        "regret": 1,
        "dead_letter": 0,
    }




def test_no_submit_projection_rows_require_verified_decision_certificate():
    from src.analysis.event_opportunity_report import build_event_opportunity_report

    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    from src.events.no_submit_projection import no_submit_projection_rows

    reactor.process_pending(decision_time=decision_time)

    assert len(no_submit_projection_rows(conn)) == 1
    report = build_event_opportunity_report(conn)
    assert report["accepted_no_submit_receipts"] == 1
    assert report["certificate_time_semantics"]["generated_no_submit_decisions"] == 1
    certificate_hash = conn.execute(
        """
        SELECT certificate_hash
        FROM decision_certificates
        WHERE certificate_type = 'PreSubmitDecisionCertificate'
        """
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO decision_certificate_supersessions (
            supersession_id, old_certificate_hash, new_certificate_hash, reason, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "supersession-current-projection",
            certificate_hash,
            "replacement-certificate-hash",
            "fixture current-certificate replacement",
            decision_time.isoformat(),
        ),
    )
    assert no_submit_projection_rows(conn) == []
    assert build_event_opportunity_report(conn)["accepted_no_submit_receipts"] == 0
    conn.execute(
        "DELETE FROM decision_certificate_supersessions WHERE supersession_id = ?",
        ("supersession-current-projection",),
    )
    assert len(no_submit_projection_rows(conn)) == 1
    conn.execute("DELETE FROM decision_certificates WHERE certificate_type = 'PreSubmitDecisionCertificate'")
    assert no_submit_projection_rows(conn) == []


def test_no_submit_receipt_ledger_is_idempotent_for_duplicate_event():
    conn, _event_store = _store()
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger

    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id="event-1",
        causal_snapshot_id="snapshot-1",
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        condition_id="condition-1",
        token_id="yes-1",
        candidate_id="candidate-1",
        executable_snapshot_id="exec-1",
        family_id="family-1",
        bin_label="70-71F",
        direction="buy_yes",
        q_live=0.8,
        q_lcb_5pct=0.7,
        c_fee_adjusted=0.4,
        c_cost_95pct=0.41,
        p_fill_lcb=0.05,
        trade_score=0.1,
        native_quote_available=True,
        source_status="MATCH",
        family_complete=True,
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fdr-family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="kelly-cost-1",
        kelly_decision_id="kelly-decision-1",
        risk_decision_id="risk-decision-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
    )
    ledger = EdliNoSubmitReceiptLedger(conn)

    ledger.insert_idempotent(receipt, decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    ledger.insert_idempotent(receipt, decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert conn.execute("SELECT COUNT(*) FROM edli_no_submit_receipts").fetchone()[0] == 1
    row = conn.execute(
        "SELECT kelly_decision_id, risk_decision_id FROM edli_no_submit_receipts WHERE event_id = 'event-1'"
    ).fetchone()
    assert row == ("kelly-decision-1", "risk-decision-1")


def test_no_submit_receipt_ledger_backfills_missing_projection_hash_on_idempotent_insert():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger, _receipt_json

    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id="event-1",
        causal_snapshot_id="snapshot-1",
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        executable_snapshot_id="exec-1",
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fdr-family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="kelly-cost-1",
        kelly_decision_id="kelly-decision-1",
        risk_decision_id="risk-decision-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
    )
    receipt_json = _receipt_json(receipt)
    conn.execute(
        """
        CREATE TABLE edli_no_submit_receipts (
            receipt_id TEXT NOT NULL PRIMARY KEY,
            event_id TEXT NOT NULL,
            final_intent_id TEXT,
            receipt_hash TEXT NOT NULL,
            projection_hash TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO edli_no_submit_receipts (
            receipt_id, event_id, final_intent_id, receipt_hash, projection_hash
        ) VALUES (?, ?, ?, ?, NULL)
        """,
        (
            "legacy-receipt-1",
            receipt.event_id,
            receipt.final_intent_id,
            hashlib.sha256(receipt_json.encode("utf-8")).hexdigest(),
        ),
    )
    ledger = EdliNoSubmitReceiptLedger(conn)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    ledger.insert_idempotent(receipt, decision_time=decision_time)

    projection_hash = conn.execute(
        "SELECT projection_hash FROM edli_no_submit_receipts WHERE event_id = 'event-1'"
    ).fetchone()[0]
    assert projection_hash


def test_no_submit_receipt_schema_backfills_projection_hash_for_existing_rows():
    from src.events.no_submit_receipts import _receipt_json
    from src.state.schema.edli_no_submit_receipts_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id="event-1",
        causal_snapshot_id="snapshot-1",
        executable_snapshot_id="exec-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
    )
    conn.execute(
        """
        CREATE TABLE edli_no_submit_receipts (
            receipt_id TEXT NOT NULL PRIMARY KEY,
            event_id TEXT NOT NULL,
            decision_time TEXT NOT NULL,
            final_intent_id TEXT,
            side_effect_status TEXT NOT NULL,
            executable_snapshot_id TEXT,
            receipt_json TEXT NOT NULL,
            receipt_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO edli_no_submit_receipts (
            receipt_id, event_id, decision_time, final_intent_id, side_effect_status,
            executable_snapshot_id, receipt_json, receipt_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "receipt-1",
            receipt.event_id,
            "2026-05-24T18:10:00+00:00",
            receipt.final_intent_id,
            receipt.side_effect_status,
            receipt.executable_snapshot_id,
            _receipt_json(receipt),
            "receipt-hash",
        ),
    )

    ensure_table(conn)

    projection_hash = conn.execute(
        "SELECT projection_hash FROM edli_no_submit_receipts WHERE receipt_id = 'receipt-1'"
    ).fetchone()[0]
    assert projection_hash


def test_no_submit_receipt_ledger_rejects_duplicate_hash_drift():
    conn, _event_store = _store()
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger, EdliReceiptHashDriftError

    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id="event-1",
        causal_snapshot_id="snapshot-1",
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fdr-family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="kelly-cost-1",
        kelly_decision_id="kelly-decision-1",
        risk_decision_id="risk-decision-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
    )
    ledger = EdliNoSubmitReceiptLedger(conn)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    ledger.insert_idempotent(receipt, decision_time=decision_time)
    drifted = replace(receipt, kelly_size_usd=2.0)

    try:
        ledger.insert_idempotent(drifted, decision_time=decision_time)
    except EdliReceiptHashDriftError as exc:
        assert "EDLI_RECEIPT_HASH_DRIFT" in str(exc)
    else:
        raise AssertionError("receipt hash drift must not be silently ignored")
    assert conn.execute("SELECT COUNT(*) FROM edli_no_submit_receipts").fetchone()[0] == 1


def test_receipt_hash_drift_dead_letters_event_before_processed():
    conn, store = _store()
    event = _forecast_event()
    store.insert_or_ignore(event)
    from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger

    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    payload = json.loads(event.payload_json)
    existing = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        city=payload.get("city"),
        target_date=payload.get("target_date"),
        metric=payload.get("metric"),
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=2.0,
        kelly_cost_basis_id="cost-1",
        kelly_decision_id="kelly-old",
        risk_decision_id="risk-old",
        final_intent_id=f"edli_intent:{event.event_id}:yes-1",
        side_effect_status="NO_SUBMIT",
    )
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(existing, decision_time=decision_time)
    reactor, rejected, _submitted = _reactor(store)

    result = reactor.process_pending(decision_time=decision_time)

    assert result.dead_lettered == 1
    assert conn.execute("SELECT COUNT(*) FROM edli_no_submit_receipts").fetchone()[0] == 1
    dead = conn.execute(
        "SELECT failure_stage, error_message FROM event_dead_letters WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert dead is not None
    assert dead[0] == "UNKNOWN_REVIEW_REQUIRED"
    assert "EDLI_RECEIPT_HASH_DRIFT" in dead[1]
    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status == "dead_letter"
    assert rejected[0][1] == "UNKNOWN_REVIEW_REQUIRED"


def test_pr332_db_concurrency_smoke_reactor_world_writes(tmp_path):
    db_path = tmp_path / "pr332-world.db"
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn)
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    forecast_events = [_forecast_event(str(index)) for index in range(6)]
    book_event = _market_event()
    for event in [*forecast_events, book_event]:
        store.insert_or_ignore(event)
    conn.commit()
    reactor, _rejected, _submitted = _reactor(store)
    writer_ready = threading.Event()
    writer_done = threading.Event()
    writer_errors = []

    def _concurrent_world_writer() -> None:
        try:
            writer_ready.set()
            writer_conn = sqlite3.connect(db_path, timeout=5.0)
            writer_conn.row_factory = sqlite3.Row
            try:
                writer_store = EventStore(writer_conn)
                future_event = _forecast_event("concurrent-future")
                future_payload = json.loads(future_event.payload_json)
                future_payload["available_at"] = "2026-05-24T18:15:00+00:00"
                future_event = replace(
                    future_event,
                    available_at="2026-05-24T18:15:00+00:00",
                    received_at="2026-05-24T18:15:01+00:00",
                    payload_json=json.dumps(future_payload, sort_keys=True, separators=(",", ":")),
                )
                writer_store.insert_or_ignore(future_event)
                writer_conn.commit()
            finally:
                writer_conn.close()
        except Exception as exc:  # pragma: no cover - assertion below reports exact failure.
            writer_errors.append(exc)
        finally:
            writer_done.set()

    thread = threading.Thread(target=_concurrent_world_writer)
    thread.start()
    assert writer_ready.wait(timeout=2.0)

    result = reactor.process_pending(decision_time=decision_time, limit=10)
    conn.commit()
    assert writer_done.wait(timeout=5.0)
    thread.join(timeout=5.0)

    assert writer_errors == []
    assert result.processed == len(forecast_events)
    assert result.rejected == 0
    rows = conn.execute(
        """
        SELECT event_id, processing_status
        FROM opportunity_event_processing
        WHERE event_id IN ({})
        """.format(",".join("?" for _ in [*forecast_events, book_event])),
        tuple(event.event_id for event in [*forecast_events, book_event]),
    ).fetchall()
    statuses = {row["event_id"]: row["processing_status"] for row in rows}
    assert {statuses[event.event_id] for event in forecast_events} == {"processed"}
    assert book_event.event_id not in statuses
    for event in forecast_events:
        cert_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM decision_certificates
            WHERE certificate_type = 'PreSubmitDecisionCertificate'
              AND json_extract(payload_json, '$.event_id') = ?
            """,
            (event.event_id,),
        ).fetchone()[0]
        receipt_count = conn.execute(
            "SELECT COUNT(*) FROM edli_no_submit_receipts WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]
        assert cert_count == 1
        assert receipt_count == 1
    regret_count = conn.execute(
        "SELECT COUNT(*) FROM no_trade_regret_events WHERE event_id = ?",
        (book_event.event_id,),
    ).fetchone()[0]
    assert regret_count == 0
    future_pending = conn.execute(
        """
        SELECT COUNT(*)
        FROM opportunity_event_processing
        WHERE processing_status = 'pending'
        """
    ).fetchone()[0]
    assert future_pending == 1


def test_processed_event_has_verified_certificate_or_failure_or_regret_or_dead_letter():
    conn, store = _store()
    accepted = _forecast_event("accepted")
    source_rejected = _forecast_event("source-rejected")
    market_rejected = _market_event()
    for event in (accepted, source_rejected, market_rejected):
        store.insert_or_ignore(event)
    reactor, _rejected, _submitted = _reactor(store)
    reactor._source_truth_gate = lambda event: event.event_id != source_rejected.event_id  # type: ignore[method-assign]

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc), limit=10)

    assert result.processed == 2
    assert result.proof_accepted == 1
    assert result.rejected == 1
    rows = conn.execute(
        """
        SELECT event_id, processing_status
        FROM opportunity_event_processing
        WHERE event_id IN (?, ?, ?)
        """,
        (accepted.event_id, source_rejected.event_id, market_rejected.event_id),
    ).fetchall()
    statuses = {row[0]: row[1] for row in rows}
    assert statuses[accepted.event_id] == "processed"
    assert statuses[source_rejected.event_id] == "processed"
    assert market_rejected.event_id not in statuses
    expected = {
        accepted.event_id: {"verified_no_submit": 1, "execution_receipt": 0, "compile_failure": 0, "regret": 0, "dead_letter": 0},
        source_rejected.event_id: {"verified_no_submit": 0, "execution_receipt": 0, "compile_failure": 1, "regret": 1, "dead_letter": 0},
        market_rejected.event_id: {"verified_no_submit": 0, "execution_receipt": 0, "compile_failure": 0, "regret": 0, "dead_letter": 0},
    }
    for event_id, expected_surfaces in expected.items():
        assert _terminal_surfaces(conn, event_id) == expected_surfaces


def test_reactor_passes_decision_time_to_submit():
    _conn, store = _store()
    event = _day0_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    seen = []
    store.insert_or_ignore(event)

    def _submit(submitted_event, submitted_decision_time):
        seen.append((submitted_event.event_id, submitted_decision_time))
        payload = json.loads(submitted_event.payload_json)
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=submitted_event.event_id,
            causal_snapshot_id=submitted_event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            final_intent_id="intent-1",
        )

    OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _event, _stage, _reason: None,
    ).process_pending(decision_time=decision_time)

    assert seen == [(event.event_id, decision_time)]


def test_sibling_family_logged_once():
    _conn, store = _store()
    store.insert_or_ignore(_day0_event("bin-a"))
    store.insert_or_ignore(_day0_event("bin-b"))
    reactor, _rejected, _submitted = _reactor(store)
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert reactor.family_log_count() == 1


def test_receipt_without_money_path_proof_is_rejected():
    _conn, store = _store()
    event = _day0_event()
    store.insert_or_ignore(event)
    rejected = []
    submitted = []

    def _submit(event, _decision_time):
        payload = json.loads(event.payload_json)
        submitted.append(event.event_id)
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=payload.get("metric"),
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1",
            family_id="family-1",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=False,
            kelly_execution_price_type="float",
            kelly_price_fee_deducted=False,
            kelly_size_usd=0.0,
            final_intent_id="intent-1",
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        config=ReactorConfig(),
    )

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert submitted == [event.event_id]
    assert result.rejected == 1
    assert rejected[0][1] == "KELLY"
    assert rejected[0][2] == "EDLI_KELLY_PROOF_MISSING"




def test_no_submit_day0_does_not_consume_tiny_cap():
    conn, store = _store()
    store.insert_or_ignore(_forecast_event("bin-a"))
    store.insert_or_ignore(_forecast_event("bin-b"))
    reactor, rejected, submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert len(submitted) == 2
    assert result.rejected == 0
    assert rejected == []
    assert conn.execute("SELECT COUNT(*) FROM edli_live_cap_usage").fetchone()[0] == 0


def test_no_submit_day0_tiny_cap_does_not_persist_across_reactor_instances():
    conn, store = _store()
    first = _forecast_event("bin-a")
    second = _forecast_event("bin-b")
    store.insert_or_ignore(first)
    reactor, _rejected, submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert submitted == [first.event_id]

    store.insert_or_ignore(second)
    second_reactor, rejected, second_submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    result = second_reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 11, tzinfo=timezone.utc))

    assert second_submitted == [second.event_id]
    assert result.rejected == 0
    assert rejected == []
    assert conn.execute("SELECT COUNT(*) FROM edli_live_cap_usage").fetchone()[0] == 0


def test_no_submit_day0_tiny_notional_cap_does_not_persist_across_reactor_instances():
    conn, store = _store()
    first = _forecast_event("bin-a")
    second = _forecast_event("bin-b")
    store.insert_or_ignore(first)
    reactor, _rejected, submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    assert submitted == [first.event_id]

    store.insert_or_ignore(second)
    second_reactor, rejected, second_submitted = _reactor(
        store,
        config=ReactorConfig(),
    )
    result = second_reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 11, tzinfo=timezone.utc))

    assert second_submitted == [second.event_id]
    assert result.rejected == 0
    assert rejected == []
    assert conn.execute("SELECT COUNT(*) FROM edli_live_cap_usage").fetchone()[0] == 0


def test_day0_source_mismatch_blocks_before_trade_score_path():
    _conn, store = _store()
    event = _day0_event()
    import json
    from dataclasses import replace

    payload = json.loads(event.payload_json)
    payload["source_match_status"] = "MISMATCH"
    mismatched = replace(
        event,
        event_id="event-source-mismatch",
        idempotency_key="idem-source-mismatch",
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    store.insert_or_ignore(mismatched)
    reactor, rejected, submitted = _reactor(store)

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.rejected == 1
    assert rejected[0][1] == "SOURCE_TRUTH"
    assert rejected[0][2] == "DAY0_HARD_FACT_AUTHORITY_BLOCKED"
    assert submitted == []


def test_reactor_does_not_write_regret_for_channel_cache_events():
    conn, store = _store()
    store.insert_or_ignore(_market_event())
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

    rejected = []
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda event, stage, reason: rejected.append((event.event_id, stage, reason)),
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert conn.execute("SELECT COUNT(*) FROM no_trade_regret_events").fetchone()[0] == 0


def test_reactor_exception_dead_letters_event():
    conn, store = _store()
    store.insert_or_ignore(_day0_event())
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: (_ for _ in ()).throw(RuntimeError("boom")),
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda _event, _stage, _reason: None,
    )

    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert result.dead_lettered == 1
    assert conn.execute("SELECT COUNT(*) FROM event_dead_letters").fetchone()[0] == 1


def _fsr_event(key_suffix: str, completeness: str, available_at: str, received_at: str):
    """Build a FORECAST_SNAPSHOT_READY event with the given completeness status."""
    import json as _json
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_id="opendata",
        source_run_id=f"run-{key_suffix}",
        cycle="00",
        track="live",
        snapshot_id=f"snap-{key_suffix}",
        snapshot_hash=f"hash-{key_suffix}",
        captured_at="2026-05-24T04:00:00+00:00",
        available_at=available_at,
        required_fields_present=True,
        required_steps_present=True,
        member_count=51 if completeness == "COMPLETE" else 10,
        min_members_floor=40,
        completeness_status=completeness,
        required_steps=[0],
        observed_steps=[0],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status=completeness,
        coverage_completeness_status=completeness,
        coverage_readiness_status="LIVE_ELIGIBLE" if completeness == "COMPLETE" else "NOT_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-05-24|high|{key_suffix}",
        source="forecast_live",
        observed_at="2026-05-24T04:00:00+00:00",
        available_at=available_at,
        received_at=received_at,
        payload=payload,
        causal_snapshot_id=f"snap-{key_suffix}",
    )


def test_partial_coverage_fsr_passes_gate_complete_fsr_dequeued_first():
    """SERVE-FRESHEST-ELIGIBLE RECONCILIATION (2026-06-11, twin-authority #8).

    The event's coverage statuses are ADVISORY — the serving authority is the
    bundle reader (tradeable-latest, 没有新的就用老的), which the adapter consults
    at proof time. A coverage-PARTIAL/BLOCKED event therefore passes the
    SOURCE_TRUTH intake gate and reaches the adapter (which rejects honestly
    when nothing eligible is servable). Live incident: 16:33:51Z six low-metric
    families dead-lettered in one second on branded PARTIAL/BLOCKED coverage
    while an eligible replacement posterior was servable.

    Ordering still holds: the COMPLETE/LIVE_ELIGIBLE event (claim Tier 1) is
    dequeued BEFORE the PARTIAL one (Tier 2) even when PARTIAL has an older
    available_at.
    """
    conn, store = _store()

    # PARTIAL event has older available_at (would sort first under naive priority+available_at order)
    partial_event = _fsr_event(
        key_suffix="partial",
        completeness="PARTIAL",
        available_at="2026-05-24T04:00:00+00:00",
        received_at="2026-05-24T04:01:00+00:00",
    )
    # COMPLETE event has newer available_at (would sort second under naive order)
    complete_event = _fsr_event(
        key_suffix="complete",
        completeness="COMPLETE",
        available_at="2026-05-24T05:00:00+00:00",
        received_at="2026-05-24T05:01:00+00:00",
    )

    store.insert_or_ignore(partial_event)
    store.insert_or_ignore(complete_event)

    submitted_order = []

    def _submit(event, _dt):
        submitted_order.append(event.event_id)
        return None  # no receipt — terminal consume downstream of the gate under test

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    dt = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    reactor.process_pending(decision_time=dt, limit=100)

    # ANTIBODY: the PARTIAL-coverage event must NOT be dead-lettered at intake —
    # it flows to the adapter, whose tradeable-latest bundle read decides.
    partial_status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (partial_event.event_id,),
    ).fetchone()[0]
    assert partial_status != "dead_letter", (
        f"PARTIAL-coverage FSR must pass the intake gate (serving authority decides), "
        f"got {partial_status}"
    )
    partial_dl = conn.execute(
        "SELECT COUNT(*) FROM event_dead_letters WHERE event_id = ?",
        (partial_event.event_id,),
    ).fetchone()[0]
    assert partial_dl == 0, "PARTIAL-coverage FSR must NOT have a dead_letter entry"

    # Both reach the adapter; the COMPLETE (Tier 1) one FIRST.
    assert complete_event.event_id in submitted_order, "COMPLETE FSR must reach submit"
    assert partial_event.event_id in submitted_order, (
        "PARTIAL-coverage FSR must reach the adapter (the serving authority, not the "
        "event payload, owns eligibility)"
    )
    assert submitted_order.index(complete_event.event_id) < submitted_order.index(partial_event.event_id), (
        "COMPLETE/LIVE_ELIGIBLE (claim Tier 1) must be dequeued before PARTIAL (Tier 2)"
    )


def test_junk_src_completeness_fsr_still_dead_letters():
    """ANTIBODY (the kept half of the intake gate): a STRUCTURALLY JUNK payload —
    source_run_completeness_status outside {COMPLETE, PARTIAL} (malformed/unknown
    producer state) — still dead-letters at intake. The serving-authority deferral
    applies only to honest, branded coverage statuses."""
    import dataclasses as _dc

    conn, store = _store()
    base = _fsr_event(
        key_suffix="junk",
        completeness="COMPLETE",
        available_at="2026-05-24T04:00:00+00:00",
        received_at="2026-05-24T04:01:00+00:00",
    )
    # Corrupt the run-identity field only (coverage stays honest).
    payload = json.loads(base.payload_json)
    payload["source_run_completeness_status"] = "GARBAGE_STATE"
    junk = _dc.replace(base, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    store.insert_or_ignore(junk)

    submitted_order = []
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda event, _dt: submitted_order.append(event.event_id),
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )
    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc), limit=10)

    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (junk.event_id,),
    ).fetchone()[0]
    assert status == "dead_letter", f"junk src_completeness must dead-letter, got {status}"
    assert junk.event_id not in submitted_order


def test_source_run_partial_window_complete_fsr_reaches_submit():
    """Run-level PARTIAL must not veto a COMPLETE/LIVE_ELIGIBLE target window."""
    conn, store = _store()
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_id="opendata",
        source_run_id="run-partial-window-complete",
        cycle="00",
        track="live",
        snapshot_id="snap-partial-window-complete",
        snapshot_hash="hash-partial-window-complete",
        captured_at="2026-05-24T04:00:00+00:00",
        available_at="2026-05-24T04:00:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="PARTIAL",
        source_run_completeness_status="PARTIAL",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high|run-partial-window-complete",
        source="forecast_live",
        observed_at="2026-05-24T04:00:00+00:00",
        available_at="2026-05-24T04:00:00+00:00",
        received_at="2026-05-24T04:01:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-partial-window-complete",
    )
    store.insert_or_ignore(event)
    submitted_order = []
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda event, _dt: submitted_order.append(event.event_id),
        reject=lambda _e, _s, _r: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )

    reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc), limit=1)

    status = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0]
    assert status != "dead_letter"
    assert event.event_id in submitted_order


def _market_channel_event(event_type: str, key_suffix: str, available_at: str = "2026-05-24T04:00:00+00:00"):
    """Build a market-channel cache-hydration event (BEST_BID_ASK_CHANGED / BOOK_SNAPSHOT)."""
    payload = MarketBookEventPayload(
        condition_id="0xcondition",
        token_id=f"token-{key_suffix}",
        outcome_label="YES",
        event_type=event_type,
        quote_seen_at=available_at,
        book_hash=f"hash-{key_suffix}",
    )
    return make_opportunity_event(
        event_type=event_type,
        entity_key=f"0xcondition|token-{key_suffix}",
        source="polymarket_market_channel",
        observed_at=available_at,
        available_at=available_at,
        received_at="2026-05-24T04:01:00+00:00",
        payload=payload,
        causal_snapshot_id=f"hash-{key_suffix}",
    )


def test_market_channel_events_do_not_starve_decision_triggers():
    """Relationship test: a large backlog of market-channel events (BEST_BID_ASK_CHANGED /
    BOOK_SNAPSHOT / NEW_MARKET_DISCOVERED) must not starve decision-trigger events
    (FORECAST_SNAPSHOT_READY, DAY0_EXTREME_UPDATED) even when market-channel events have
    an older available_at and would normally sort first within the same priority level.

    The fetch_pending ORDER BY must assign market-channel events to a lower tier (tier 2)
    than decision-trigger events (tier 0 / tier 1), so the per-cycle budget (limit) is
    consumed by decision events first.

    Invariant tested: with N_MC > limit market-channel events older than a DAY0_EXTREME_UPDATED
    event, fetch_pending(limit=10) must include the DAY0 event and NOT fill all 10 slots with
    market-channel events.

    RED (without fix): BEST_BID_ASK_CHANGED events have older available_at → sort first at
    tier=1 (same as DAY0); all 10 limit slots consumed by MC; DAY0 never fetched.
    GREEN (with fix): MC events demoted to tier=2; DAY0 at tier=1 fetched before any MC event.
    """
    conn, store = _store()

    # Insert N_MC BEST_BID_ASK_CHANGED events with older available_at (tier 2 with fix)
    N_MC = 30  # exceeds limit=10; without MC demotion, all 10 slots go to MC
    for i in range(N_MC):
        ev = _market_channel_event(
            "BEST_BID_ASK_CHANGED",
            key_suffix=str(i),
            available_at="2026-05-24T01:00:00+00:00",  # older than DAY0 event
        )
        store.insert_or_ignore(ev)

    # Insert one DAY0_EXTREME_UPDATED with newer available_at (tier 1 — must not be starved)
    day0 = _day0_event(key_suffix="starvation-test")
    store.insert_or_ignore(day0)

    dt = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    fetched = store.fetch_pending(decision_time=dt.isoformat(), limit=10)

    fetched_types = [e.event_type for e in fetched]
    day0_fetched = any(e.event_id == day0.event_id for e in fetched)
    mc_count = fetched_types.count("BEST_BID_ASK_CHANGED")

    assert day0_fetched, (
        f"DAY0_EXTREME_UPDATED was not fetched: market-channel events starved it. "
        f"fetched event_types={fetched_types[:10]}"
    )
    assert mc_count < 10, (
        f"All 10 fetch slots consumed by BEST_BID_ASK_CHANGED ({mc_count}); "
        f"decision-trigger event starved."
    )


def test_market_channel_events_do_not_starve_fsr():
    """Relationship test: COMPLETE FSR events must be fetched before market-channel events
    due to tier-0 priority, even with a large MC backlog of older events.

    This test covers the COMPLETE FSR path (tier 0, unconditionally first).
    See test_market_channel_events_do_not_starve_decision_triggers for the tier-1 starvation
    case (DAY0_EXTREME_UPDATED / other decision events vs MC).
    """
    conn, store = _store()

    # 30 MC events older than FSR
    N_MC = 30
    for i in range(N_MC):
        ev = _market_channel_event(
            "BEST_BID_ASK_CHANGED",
            key_suffix=str(i),
            available_at="2026-05-24T01:00:00+00:00",
        )
        store.insert_or_ignore(ev)

    # 1 COMPLETE FSR, newer available_at
    fsr = _fsr_event(
        key_suffix="sole-fsr",
        completeness="COMPLETE",
        available_at="2026-05-24T05:00:00+00:00",
        received_at="2026-05-24T05:01:00+00:00",
    )
    store.insert_or_ignore(fsr)

    dt = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    fetched = store.fetch_pending(decision_time=dt.isoformat(), limit=10)

    fetched_ids = [e.event_id for e in fetched]
    assert fsr.event_id in fetched_ids, (
        f"COMPLETE FSR not in top-10 fetch despite tier-0 priority. "
        f"Fetched types: {[e.event_type for e in fetched][:10]}"
    )


def test_reactor_overfetches_before_lane_interleave_under_day0_flood():
    """A small process limit must not truncate the forecast lane before interleave.

    Live regression: ``reactor_process_limit`` was ~10 while fetch_pending returned
    12+ tradeable Day0 rows before the first FORECAST/REDECISION row.  The reactor
    interleave never saw the forecast lane, so ordinary entry/redecision work
    starved even though the fairness helper was correct for a full page.
    """

    conn, store = _store()
    available_at = "2026-05-25T06:00:00+00:00"
    for i in range(12):
        store.insert_or_ignore(
            _day0_event_for_target(
                key_suffix=f"day0-{i}",
                target_date="2026-05-25",
                available_at=available_at,
            )
        )
    fsr = _forecast_event(key_suffix="fsr-behind-day0", target_date="2026-05-25")
    store.insert_or_ignore(fsr)

    requested_limits: list[int] = []
    original_fetch = store.fetch_pending

    def _recording_fetch(**kwargs):
        requested_limits.append(int(kwargs["limit"]))
        return original_fetch(**kwargs)

    store.fetch_pending = _recording_fetch  # type: ignore[method-assign]
    reactor, _rejected, submitted = _reactor(
        store,
        config=ReactorConfig(),
    )

    reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert requested_limits and requested_limits[0] > 1
    assert submitted == [fsr.event_id], (
        "forecast/redecision lane must receive the guaranteed first processed "
        f"slot even when Day0 occupies the first small fetch page; submitted={submitted}"
    )


# --- antibody: reactor._build_regret_envelope_json must not mutate store.conn.row_factory --------
# Task #42 (2026-06-11): same footgun as the PRAGMA busy_timeout leak in the claim storm — a
# connection-global attribute mutated inside a shared-conn path is visible to every concurrent
# reader.  The cursor-local row_factory approach removes the mutation entirely.


def _sentinel_row_factory(cursor, row):  # noqa: ARG001
    """Detectable sentinel factory — identity observable with 'is'."""
    return row


def test_build_regret_envelope_json_does_not_mutate_store_conn_row_factory():
    """ANTIBODY (Task #42): reactor._build_regret_envelope_json snapshot fetch must not set
    store.conn.row_factory.  A sentinel factory pinned before the call must survive after it,
    including through the sqlite3.Error exception path (table absent)."""
    conn, store = _store()
    # Pin a sentinel — not sqlite3.Row, not None; identity is the assertion.
    conn.row_factory = _sentinel_row_factory

    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    reactor, rejected, _submitted = _reactor(store, gates=False)

    event = _day0_event()
    store.insert_or_ignore(event)

    # Process; the rejection writes a regret row and calls _build_regret_envelope_json,
    # which tries to fetch from executable_market_snapshots (absent in the in-memory schema =>
    # the query either returns None or raises — in both cases conn.row_factory must be untouched).
    reactor.process_pending(decision_time=decision_time, limit=1)

    assert conn.row_factory is _sentinel_row_factory, (
        "reactor._build_regret_envelope_json mutated store.conn.row_factory — "
        "cursor-local row_factory must be used instead of conn-level save/restore"
    )


# --- antibody: multi-winner auction loop (docs/operations/current/plans/ -----------------------
# auction_multiwinner_plan_2026-07-19.md §5, items 1 and 5). process_pending's global-batch
# branch now loops the existing, unmodified single-winner epoch back-to-back within one wake
# instead of returning after exactly one epoch. These tests drive that loop directly through a
# fake ``process_global_batch`` adapter (same seam every other global-batch test in this file
# fakes), proving the loop's per-epoch isolation and its stop conditions — not re-testing the
# real solver/collateral/actuation stack, which has its own coverage elsewhere
# (tests/integration/test_w3_solve_seam_g3.py, tests/test_collateral_ledger.py,
# tests/test_command_recovery.py carry the reservation/wealth-witness/command-recovery
# antibodies for this same plan).


def _multiwinner_events(prefix: str, count: int):
    return tuple(
        _forecast_event(f"{prefix}-{index}", target_date=f"2026-05-{25 + index}")
        for index in range(count)
    )


def _requeue_losers_finalize(reactor):
    """A ``_finalize_deferred_event_unit`` fake: winners count as accepted and
    stay claimed (consumed); losers are put back to PENDING via the real store
    API (the same outcome the production transient-retry path already
    produces for a ``SUBMIT_ABORTED_PRICE_MOVED`` reason — see
    test_global_batch_claims_epoch_then_calls_one_lock_free_batch_seam above).
    Isolates these tests from the certificate/proof-bundle plumbing
    ``_process_one_post_submit`` requires for a real accept, which is covered
    elsewhere."""

    def _finalize(
        event,
        receipt,
        *,
        decision_time,
        result,
        wait_ms=None,
        continuation_event=None,
        claim_generation=None,
        claim_attempt_count=None,
    ):
        del (
            decision_time,
            wait_ms,
            continuation_event,
            claim_generation,
            claim_attempt_count,
        )
        if receipt.submitted:
            result.proof_accepted += 1
        else:
            reactor._store.requeue_pending(event.event_id, last_error=receipt.reason)
            result.retried += 1
        return True

    return _finalize


def _terminal_losers_finalize(reactor):
    """A ``_finalize_deferred_event_unit`` fake for the production-lifecycle
    antibodies below (2026-07-19 external review,
    ~/cgc-answers/2026-07-19_zeus-multiwinner-auction-merge-gate/answer.md,
    BLOCKER "non-adversarial validation" — tests/events/test_reactor.py:
    test_multiwinner_*): winners count as accepted and stay claimed
    (consumed), matching the real reactor's terminal winner disposition.
    Losers are marked PROCESSED (terminal) — the production disposition for
    a real ``GLOBAL_NOT_SELECTED`` receipt (src/engine/global_batch_runtime.py
    :3394-3405) — NOT requeued under a fabricated transient reason. A
    terminalized loser is unavailable to any later epoch's re-fetch, exactly
    like production; ``_requeue_losers_finalize`` above instead requeues
    losers so the SAME candidate set can fake K sequential winners from a
    pool production would actually exhaust after one epoch. Isolates these
    tests from the certificate/proof-bundle plumbing
    ``_process_one_post_submit`` requires for a real accept, which is covered
    elsewhere."""

    def _finalize(
        event,
        receipt,
        *,
        decision_time,
        result,
        wait_ms=None,
        continuation_event=None,
        claim_generation=None,
        claim_attempt_count=None,
    ):
        del (
            decision_time,
            wait_ms,
            continuation_event,
            claim_generation,
            claim_attempt_count,
        )
        if receipt.submitted:
            result.proof_accepted += 1
        else:
            reactor._store.mark_processed(event.event_id)
            result.rejected += 1
        return True

    return _finalize


def _serialized_frontier_finalize(reactor):
    """Model the production winner-frontier disposition without proof plumbing."""

    def _finalize(
        event,
        receipt,
        *,
        decision_time,
        result,
        wait_ms=None,
        continuation_event=None,
        claim_generation=None,
        claim_attempt_count=None,
    ):
        del decision_time, wait_ms, claim_generation, claim_attempt_count
        if receipt.submitted:
            result.proof_accepted += 1
            reactor._store.mark_processed(event.event_id)
            if continuation_event is not None:
                assert reactor._store.insert_or_ignore(continuation_event)
                reactor._store.requeue_pending(
                    continuation_event.event_id,
                    last_error=GLOBAL_WINNER_TARGETED_CLAIM,
                )
        elif _is_transient_money_path_reason(receipt.reason):
            reactor._store.requeue_pending(
                event.event_id,
                last_error=receipt.reason,
            )
            result.retried += 1
        else:
            reactor._store.mark_processed(event.event_id)
            result.rejected += 1
        return True

    return _finalize


def _multiwinner_reactor(store, process_global_batch):
    def _direct_submit(*_args, **_kwargs):
        pytest.fail("global batch path must not invoke per-event submit")

    _direct_submit.process_global_batch = process_global_batch  # type: ignore[attr-defined]
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_direct_submit,
        reject=lambda *_args: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    reactor._finalize_deferred_event_unit = _requeue_losers_finalize(reactor)
    return reactor


def test_global_unknown_side_effect_winner_finalizes_before_no_submit_loser():
    conn, store = _store()
    loser, winner = _multiwinner_events("unknown-side-effect-order", 2)
    for event in (loser, winner):
        store.insert_or_ignore(event)

    def _batch(claimed, _decision_time, *, claim_unpaged_winner=None):
        del claim_unpaged_winner
        return GlobalBatchSubmitResult(
            receipts={
                loser.event_id: EventSubmissionReceipt(
                    submitted=False,
                    event_id=loser.event_id,
                    causal_snapshot_id=loser.causal_snapshot_id,
                    reason="GLOBAL_NOT_SELECTED:unknown-winner",
                    proof_accepted=False,
                ),
                winner.event_id: EventSubmissionReceipt(
                    submitted=False,
                    event_id=winner.event_id,
                    causal_snapshot_id=winner.causal_snapshot_id,
                    reason="POST_SUBMIT_UNKNOWN:test",
                    proof_accepted=False,
                    side_effect_status="POST_SUBMIT_UNKNOWN",
                    venue_call_started=True,
                    venue_ack_received=False,
                ),
            },
            winner_event_id=winner.event_id,
            venue_submit_count=0,
        )

    reactor = _multiwinner_reactor(store, _batch)
    finalized_ids = []

    def _finalize(
        event,
        _receipt,
        *,
        decision_time,
        result,
        wait_ms=None,
        continuation_event=None,
        claim_generation=None,
        claim_attempt_count=None,
    ):
        del (
            decision_time,
            result,
            wait_ms,
            continuation_event,
            claim_generation,
            claim_attempt_count,
        )
        finalized_ids.append(event.event_id)
        reactor._store.mark_processed(event.event_id)
        return True

    reactor._finalize_deferred_event_unit = _finalize
    outcome = reactor._process_global_event_batch(
        (loser, winner),
        decision_time=_DT_VENUE_OPEN,
        result=ReactorResult(),
        budget=None,
        cycle_start=time.monotonic(),
        remaining=2,
        already_charged_event_ids=frozenset(),
        cancelled=lambda: False,
    )

    assert outcome.submitted is False
    assert finalized_ids == [winner.event_id, loser.event_id]
    assert _processing_status(conn, winner.event_id) == "processed"
    assert _processing_status(conn, loser.event_id) == "processed"


def test_global_winner_finalize_commits_fresh_frontier_with_terminal_carrier():
    conn, store = _store()
    event = _multiwinner_events("frontier-finalize", 1)[0]
    store.insert_or_ignore(event)
    assert store.claim(event.event_id, claimed_at=_DT_VENUE_OPEN.isoformat())
    continuation = make_opportunity_event(
        event_type=event.event_type,
        entity_key=event.entity_key,
        source=f"test_global_continuation:{event.event_id}",
        observed_at=event.observed_at,
        available_at=event.available_at,
        received_at=_DT_VENUE_OPEN.isoformat(),
        causal_snapshot_id=event.causal_snapshot_id,
        payload=json.loads(event.payload_json),
        priority=event.priority,
        created_at=_DT_VENUE_OPEN.isoformat(),
    )
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda *_args: None,
        reject=lambda *_args: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    def _accepted(_event, _receipt, *, decision_time, result):
        del decision_time
        result.proof_accepted += 1
        return None

    reactor._process_one_post_submit = _accepted
    result = ReactorResult()
    finalized = reactor._finalize_deferred_event_unit(
        event,
        EventSubmissionReceipt(
            submitted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="VENUE_SUBMIT_ACKED",
            venue_call_started=True,
            venue_ack_received=True,
        ),
        decision_time=_DT_VENUE_OPEN,
        result=result,
        continuation_event=continuation,
    )

    assert finalized is True
    assert result.proof_accepted == 1
    assert result.processed == 1
    assert _processing_status(conn, event.event_id) == "processed"
    assert _processing_status(conn, continuation.event_id) == "pending"
    assert (
        store.processing_last_error(continuation.event_id)
        == GLOBAL_WINNER_TARGETED_CLAIM
    )
    assert conn.in_transaction is False


def test_global_winner_side_effect_advances_frontier_while_proof_retries():
    conn, store = _store()
    event = _multiwinner_events("frontier-side-effect", 1)[0]
    store.insert_or_ignore(event)
    assert store.claim(event.event_id, claimed_at=_DT_VENUE_OPEN.isoformat())
    continuation = make_opportunity_event(
        event_type=event.event_type,
        entity_key=event.entity_key,
        source=f"test_global_continuation:{event.event_id}",
        observed_at=event.observed_at,
        available_at=event.available_at,
        received_at=_DT_VENUE_OPEN.isoformat(),
        causal_snapshot_id=event.causal_snapshot_id,
        payload=json.loads(event.payload_json),
        priority=event.priority,
        created_at=_DT_VENUE_OPEN.isoformat(),
    )
    reservation_ledger = PortfolioReservationLedger()
    reservation_ledger.reserve(event.event_id, "Test City", 7.5)

    def _submit(*_args):
        return None

    _submit.reservation_ledger = reservation_ledger
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_args: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    reactor._transient_requeue_reasons[event.event_id] = (
        "ADMISSION_BUY_NO_GLOBAL_CURRENT_STATE_INVALID:"
        "receipt_scalar_mismatch"
    )
    reactor._process_one_post_submit = (
        lambda *_args, **_kwargs: _EXECUTABLE_SNAPSHOT_RETRY
    )
    result = ReactorResult()
    finalized = reactor._finalize_deferred_event_unit(
        event,
        EventSubmissionReceipt(
            submitted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            proof_accepted=False,
            side_effect_status="VENUE_SUBMIT_ACKED",
            venue_call_started=True,
            venue_ack_received=True,
        ),
        decision_time=_DT_VENUE_OPEN,
        result=result,
        continuation_event=continuation,
    )

    assert finalized is True
    assert result.proof_accepted == 0
    assert result.processed == 0
    assert result.dead_lettered == 0
    assert result.retried == 1
    assert list(reservation_ledger) == [("Test City", 7.5)]
    reservation_ledger.rollback(event.event_id)
    assert list(reservation_ledger) == [("Test City", 7.5)]
    assert _processing_status(conn, event.event_id) == "pending"
    assert _processing_status(conn, continuation.event_id) == "pending"
    assert (
        store.processing_last_error(continuation.event_id)
        == GLOBAL_WINNER_TARGETED_CLAIM
    )
    assert conn.in_transaction is False


def test_global_winner_continuation_write_failure_rolls_back_window_b(monkeypatch):
    conn, store = _store()
    event = _multiwinner_events("frontier-write-failure", 1)[0]
    store.insert_or_ignore(event)
    assert store.claim(event.event_id, claimed_at=_DT_VENUE_OPEN.isoformat())
    continuation = make_opportunity_event(
        event_type=event.event_type,
        entity_key=event.entity_key,
        source=f"test_global_continuation:{event.event_id}",
        observed_at=event.observed_at,
        available_at=event.available_at,
        received_at=_DT_VENUE_OPEN.isoformat(),
        causal_snapshot_id=event.causal_snapshot_id,
        payload=json.loads(event.payload_json),
        priority=event.priority,
        created_at=_DT_VENUE_OPEN.isoformat(),
    )
    reservation_ledger = PortfolioReservationLedger()
    reservation_ledger.reserve(event.event_id, "Test City", 7.5)

    def _submit(*_args):
        return None

    _submit.reservation_ledger = reservation_ledger
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_args: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    def _accepted(_event, _receipt, *, decision_time, result):
        del decision_time
        result.proof_accepted += 1
        return None

    reactor._process_one_post_submit = _accepted
    original_insert = store.insert_or_ignore

    def _fail_continuation_insert(candidate):
        if candidate.event_id == continuation.event_id:
            raise sqlite3.OperationalError("database is locked")
        return original_insert(candidate)

    monkeypatch.setattr(store, "insert_or_ignore", _fail_continuation_insert)
    result = ReactorResult()
    finalized = reactor._finalize_deferred_event_unit(
        event,
        EventSubmissionReceipt(
            submitted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="VENUE_SUBMIT_ACKED",
            venue_call_started=True,
            venue_ack_received=True,
        ),
        decision_time=_DT_VENUE_OPEN,
        result=result,
        continuation_event=continuation,
    )

    assert finalized is False
    assert result.proof_accepted == 0
    assert result.processed == 0
    assert result.retried == 1
    assert result.rejection_reasons == [
        "WORLD_WRITE_LOCK_BUSY_POST_SUBMIT"
    ]
    assert list(reservation_ledger) == [("Test City", 7.5)]
    reservation_ledger.rollback(event.event_id)
    assert list(reservation_ledger) == [("Test City", 7.5)]
    assert _processing_status(conn, event.event_id) == "pending"
    assert (
        conn.execute(
            "SELECT 1 FROM opportunity_events WHERE event_id = ?",
            (continuation.event_id,),
        ).fetchone()
        is None
    )
    assert conn.in_transaction is False


def _sequential_winner_batch(claimed, _decision_time, *, claim_unpaged_winner=None, on_winner=None):
    """Fake ``process_global_batch``: pick the lexically-first still-pending
    event as this epoch's winner (submits), requeue the rest transiently —
    models K sequential winners draining a candidate pool one epoch at a
    time, exactly the loop's re-decision lane."""

    del claim_unpaged_winner
    winner = min(claimed, key=lambda event: event.event_id)
    if on_winner is not None:
        on_winner(winner)
    receipts = {
        winner.event_id: EventSubmissionReceipt(
            submitted=True,
            event_id=winner.event_id,
            causal_snapshot_id=winner.causal_snapshot_id,
            side_effect_status="VENUE_SUBMIT_ACKED",
            venue_call_started=True,
            venue_ack_received=True,
            reason="TEST_WINNER_SUBMITTED",
        )
    }
    for event in claimed:
        if event.event_id == winner.event_id:
            continue
        receipts[event.event_id] = EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            reason="SUBMIT_ABORTED_PRICE_MOVED:GLOBAL_TEST_LOSER_REQUEUE",
            proof_accepted=False,
        )
    return GlobalBatchSubmitResult(
        receipts=receipts,
        winner_event_id=winner.event_id,
        venue_submit_count=1,
    )


def test_multiwinner_loop_double_submit_impossible_within_wake():
    """ANTIBODY #1 (auction_multiwinner_plan_2026-07-19 §5, item 1): two
    consecutive epochs in one wake never have two commands mid-submit. Each
    epoch mints a FRESH GlobalOneShotActuator — the real production one-shot
    capability (src/engine/global_batch_runtime.py:178-189) — and a second
    ``consume()`` on a PRIOR epoch's actuator still raises
    GLOBAL_ACTUATION_CAPABILITY_CONSUMED. Total venue submits over the wake
    == number of submitting epochs, each == exactly 1."""
    from src.engine.global_batch_runtime import GlobalOneShotActuator

    conn, store = _store()
    events = _multiwinner_events("mw", 3)
    for event in events:
        store.insert_or_ignore(event)

    observations = {"batch_calls": 0, "actuators": [], "submitted_event_ids": []}

    def _process_global_batch(claimed, decision_time, *, claim_unpaged_winner=None):
        observations["batch_calls"] += 1

        def _on_winner(winner):
            actuator = GlobalOneShotActuator(lambda: None)
            actuator.consume()
            observations["actuators"].append(actuator)
            observations["submitted_event_ids"].append(winner.event_id)
            # ONE-SHOT within this epoch: a second consume() on THIS epoch's
            # actuator raises immediately, even misused inside its own epoch.
            with pytest.raises(RuntimeError, match="GLOBAL_ACTUATION_CAPABILITY_CONSUMED"):
                actuator.consume()

        return _sequential_winner_batch(
            claimed, decision_time, claim_unpaged_winner=claim_unpaged_winner, on_winner=_on_winner
        )

    reactor = _multiwinner_reactor(store, _process_global_batch)
    reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=None)

    # K=3 winners, one epoch per event — three fresh actuators, three
    # submitted receipts, no epoch ever produced more than one submit.
    assert observations["batch_calls"] == 3
    assert observations["submitted_event_ids"] == sorted(
        event.event_id for event in events
    )
    assert len(observations["actuators"]) == 3
    assert len({id(actuator) for actuator in observations["actuators"]}) == 3

    # A STALE actuator from ANY prior epoch stays permanently consumed — reuse
    # from a later epoch's context still raises.
    for actuator in observations["actuators"]:
        with pytest.raises(RuntimeError, match="GLOBAL_ACTUATION_CAPABILITY_CONSUMED"):
            actuator.consume()

    assert all(
        _processing_status(conn, event.event_id) == "processing" for event in events
    ), "all three winners must be consumed (claimed, never returned to pending)"


def test_multiwinner_loop_stops_on_preemption_leaving_remainder_pending():
    """ANTIBODY #5a (loop preemption): a tripped ``cycle_cancelled()`` stops
    the loop mid-wake — checked with the SAME hook a single wake already
    honors — leaving the remaining candidates PENDING for the next wake."""
    conn, store = _store()
    events = _multiwinner_events("pre", 3)
    for event in events:
        store.insert_or_ignore(event)

    # ``cancelled`` fires True only once epoch #1's process_global_batch has
    # actually run (never mid-claim — _process_global_event_batch's OWN claim
    # loop also polls this same hook per event, so tying the trip to the batch
    # call itself, not a raw call count, is what isolates "preempt strictly
    # BETWEEN epochs" from "preempt mid-claim").
    batch_calls = {"n": 0}

    def _counting_batch(claimed, decision_time, *, claim_unpaged_winner=None):
        outcome = _sequential_winner_batch(
            claimed, decision_time, claim_unpaged_winner=claim_unpaged_winner
        )
        batch_calls["n"] += 1
        return outcome

    reactor = _multiwinner_reactor(store, _counting_batch)

    def _cancelled():
        return batch_calls["n"] >= 1

    reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=None, cancelled=_cancelled)

    statuses = {event.event_id: _processing_status(conn, event.event_id) for event in events}
    assert sorted(statuses.values()) == ["pending", "pending", "processing"], (
        "preemption must stop after exactly one submitting epoch, leaving the "
        f"rest PENDING for the next wake: {statuses}"
    )


def test_multiwinner_loop_stops_on_elapsed_wall_clock_budget(monkeypatch):
    """ANTIBODY #5b (loop budget): an elapsed per-wake wall-clock budget stops
    the loop mid-wake — the SAME existing guard that already bounds a single
    wake — leaving the remaining candidates PENDING for the next wake."""
    monkeypatch.setenv("ZEUS_REACTOR_CYCLE_BUDGET_SECONDS", "0.05")
    conn, store = _store()
    events = _multiwinner_events("budget", 3)
    for event in events:
        store.insert_or_ignore(event)

    def _slow_batch(claimed, decision_time, *, claim_unpaged_winner=None):
        time.sleep(0.1)  # spend the tiny budget during epoch #1
        return _sequential_winner_batch(claimed, decision_time, claim_unpaged_winner=claim_unpaged_winner)

    reactor = _multiwinner_reactor(store, _slow_batch)

    reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=None)

    statuses = {event.event_id: _processing_status(conn, event.event_id) for event in events}
    assert sorted(statuses.values()) == ["pending", "pending", "processing"], (
        "an elapsed wake budget must stop after exactly one submitting epoch, "
        f"leaving the rest PENDING for the next wake: {statuses}"
    )


# --- BLOCKER FIX antibodies (2026-07-19 external review, -------------------------------------
# ~/cgc-answers/2026-07-19_zeus-multiwinner-auction-merge-gate/answer.md,
# §BLOCKER "progress and ordering", reactor.py:967-981,1216-1279,1313-1482 +
# BLOCKER "non-adversarial validation", tests/events/test_reactor.py:test_multiwinner_*).
# The three tests above prove the loop's per-epoch isolation using a fabricated transient
# loser-requeue and an unbounded limit=None — exactly what the review flags as
# non-adversarial: production terminalizes losers (GLOBAL_NOT_SELECTED) and runs with a
# finite process limit, and venue_call_started ("submitted") alone let epoch K+1 start even
# when K's winner world-side finalization never durably committed. The two tests below drive
# process_pending with the PRODUCTION disposition classifier (a real GLOBAL_NOT_SELECTED
# terminal receipt for losers, not a fabricated requeue reason) and a finite, production-like
# limit, closing the two gaps the fix makes testable.


def test_multiwinner_loop_breaks_when_winner_finalization_is_not_durable(monkeypatch):
    """Epoch K's winner receipt/finalization write does not commit here:
    ``_process_one_post_submit`` (the call inside the REAL, unmodified
    ``_finalize_deferred_event_unit`` Window-B save-point) raises a genuine
    ``sqlite3.OperationalError("database is locked")`` — the exact
    exception, and the exact real exception-handling machinery (classified
    by ``_is_sqlite_lock_error``, rolled back, requeued via the real
    ``EventStore.requeue_pending``), that a real Window-B world-write lock
    produces; only the trigger is injected, not the outcome. Before the fix,
    ``submitted`` (venue_call_started) alone let the loop continue to epoch
    K+1 regardless of whether K's winner world disposition ever became
    durable.

    Two candidates exist so the test can PROVE "no K+1 auction ran" rather
    than merely "no more candidates existed": the fetch PAGE is forced to
    exactly one row (``ZEUS_REACTOR_FETCH_BATCH_LIMIT=1``) with
    ``limit=None`` (no event-count budget at all — remaining stays None the
    whole wake, so nothing about the event-count budget can explain a stop),
    so epoch #1 claims and auctions ONLY the first candidate; the second
    candidate is never even fetched. The fix must BREAK the loop after
    epoch #1: no second ``process_global_batch`` call (which would have
    re-fetched and reached the second candidate), and the winner event is
    requeued to PENDING — not durably processed, not dead-lettered."""
    monkeypatch.setenv("ZEUS_REACTOR_FETCH_BATCH_LIMIT", "1")
    conn, store = _store()
    events = _multiwinner_events("finalize-lock", 2)
    for event in events:
        store.insert_or_ignore(event)

    batch_calls = {"n": 0}
    winner_holder: dict[str, str] = {}

    def _batch(claimed, _decision_time, *, claim_unpaged_winner=None):
        del claim_unpaged_winner
        batch_calls["n"] += 1
        if batch_calls["n"] > 1:
            pytest.fail(
                "process_global_batch must not run a second epoch after "
                "epoch #1's winner finalization failed to commit"
            )
        assert len(claimed) == 1, (
            "the forced 1-row fetch page must hand exactly one candidate to "
            f"epoch #1: got {[event.event_id for event in claimed]}"
        )
        winner = claimed[0]
        winner_holder["event_id"] = winner.event_id
        return GlobalBatchSubmitResult(
            receipts={
                winner.event_id: EventSubmissionReceipt(
                    submitted=True,
                    event_id=winner.event_id,
                    causal_snapshot_id=winner.causal_snapshot_id,
                    side_effect_status="VENUE_SUBMIT_ACKED",
                    venue_call_started=True,
                    venue_ack_received=True,
                    reason="TEST_WINNER_SUBMITTED",
                )
            },
            winner_event_id=winner.event_id,
            venue_submit_count=1,
        )

    def _direct_submit(*_a, **_k):
        pytest.fail("global batch path must not invoke per-event submit")

    _direct_submit.process_global_batch = _batch  # type: ignore[attr-defined]
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_direct_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )

    def _raise_world_write_lock_busy(_event, _submit_result, *, decision_time, result):
        del decision_time, result
        raise sqlite3.OperationalError("database is locked")

    reactor._process_one_post_submit = _raise_world_write_lock_busy

    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=None)

    assert batch_calls["n"] == 1, (
        "the loop must break after epoch #1's winner finalization failed to "
        "commit durably — no epoch #2 auction"
    )
    winner_id = winner_holder["event_id"]
    untouched_id = next(
        event.event_id for event in events if event.event_id != winner_id
    )
    assert _processing_status(conn, untouched_id) == "pending", (
        "the second candidate must never even be fetched — proving the loop "
        "stopped because K's finalization failed, not because no more "
        "candidates existed"
    )
    assert result.processed == 0 and result.dead_lettered == 0, (
        "the winner event must NOT be durably closed (processed or "
        f"dead-lettered) when its world-side finalization failed to commit: {result}"
    )
    assert _processing_status(conn, winner_id) == "pending", (
        "the winner event must be requeued to PENDING (claimable next "
        "wake), never left in a durably closed disposition, when its "
        f"world-side finalization did not commit: {result}"
    )


def test_multiwinner_loop_debits_finite_budget_once_per_claimed_event():
    """The pre-fix debit (``remaining -= epoch.attempted``, the raw SCANNED
    count) drains a finite, production-like process limit on an event that
    was scanned but never claimed (e.g. a transient claim-lock bounce),
    stranding it PENDING and unreachable for the rest of the wake even
    though the epoch's real claimed work used less than the whole budget.
    Here candidate #2 bounces its FIRST claim attempt (a real, transient
    miss via the SAME ``result.claim_lock_bounces``/retry accounting
    production uses — not a hand-picked winner), while the other two
    candidates are claimed, auctioned, and finalized through the PRODUCTION
    disposition classifier: one submitted winner, one real terminal
    ``GLOBAL_NOT_SELECTED`` loser (not the fabricated transient
    loser-requeue the earlier fixtures use). With a finite ``limit`` sized
    exactly to the 3 real candidates, the fixed debit (claimed events,
    deduped across epochs) must still leave enough budget for the bounced
    candidate's second, successful claim to run as its own epoch — the loop
    must not be unable to page past the budget."""
    conn, store = _store()
    events = _multiwinner_events("pagebudget", 3)
    for event in events:
        store.insert_or_ignore(event)
    bounce_id = events[1].event_id

    batch_calls = {"n": 0}

    def _batch(claimed, _decision_time, *, claim_unpaged_winner=None):
        del claim_unpaged_winner
        batch_calls["n"] += 1
        winner = min(claimed, key=lambda event: event.event_id)
        receipts = {
            winner.event_id: EventSubmissionReceipt(
                submitted=True,
                event_id=winner.event_id,
                causal_snapshot_id=winner.causal_snapshot_id,
                side_effect_status="VENUE_SUBMIT_ACKED",
                venue_call_started=True,
                venue_ack_received=True,
                reason="TEST_WINNER_SUBMITTED",
            )
        }
        for event in claimed:
            if event.event_id == winner.event_id:
                continue
            receipts[event.event_id] = EventSubmissionReceipt(
                submitted=False,
                event_id=event.event_id,
                causal_snapshot_id=event.causal_snapshot_id,
                reason=f"GLOBAL_NOT_SELECTED:{winner.event_id}",
                proof_accepted=False,
            )
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=winner.event_id,
            venue_submit_count=1,
        )

    def _direct_submit(*_a, **_k):
        pytest.fail("global batch path must not invoke per-event submit")

    _direct_submit.process_global_batch = _batch  # type: ignore[attr-defined]
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_direct_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    reactor._finalize_deferred_event_unit = _terminal_losers_finalize(reactor)

    real_process_event_unit = reactor._process_event_unit
    bounced = {"done": False}

    def _bounce_once_then_real(event, *, decision_time, result, defer_submit=False):
        if event.event_id == bounce_id and not bounced["done"]:
            bounced["done"] = True
            result.claim_lock_bounces += 1
            result.retried += 1
            return None
        return real_process_event_unit(
            event, decision_time=decision_time, result=result, defer_submit=defer_submit
        )

    reactor._process_event_unit = _bounce_once_then_real

    reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=3)

    assert batch_calls["n"] == 2, (
        "the bounced candidate's second, successful claim must still run as "
        "its own epoch within the SAME finite limit=3 budget: the fixed "
        "debit must not already have spent the whole allowance on epoch "
        "#1's 3 SCANS when only 2 were ever actually claimed"
    )
    statuses = {event.event_id: _processing_status(conn, event.event_id) for event in events}
    assert statuses[bounce_id] == "processing", (
        "the bounced candidate must have been reclaimed and consumed as its "
        f"own winner in epoch #2: {statuses}"
    )


def test_multiwinner_winner_frontier_survives_spent_budget_and_advances_causal_cut(
    monkeypatch,
):
    """Each durable winner transfers one frontier until a fresh CASH proof.

    The second and third source carriers become available only after known
    monotonic elapsed intervals. This catches both stale wake-start cuts and an
    erroneous jump to the machine's present wall date (which would cross the
    fixture market horizon).
    """

    monkeypatch.setenv("ZEUS_REACTOR_FETCH_BATCH_LIMIT", "1")
    clock = {"value": 100.0}
    monkeypatch.setattr("src.events.reactor.time.monotonic", lambda: clock["value"])

    conn, store = _store()
    raw_events = _multiwinner_events("durable-frontier", 3)
    available_offsets = (0.0, 2.0, 5.0)
    events = tuple(
        replace(
            event,
            available_at=(_DT_VENUE_OPEN + timedelta(seconds=offset)).isoformat(),
            received_at=(_DT_VENUE_OPEN + timedelta(seconds=offset)).isoformat(),
        )
        for event, offset in zip(raw_events, available_offsets, strict=True)
    )
    for event in events:
        store.insert_or_ignore(event)

    observations: dict[str, list] = {
        "decision_times": [],
        "winner_ids": [],
        "claimed_ids": [],
        "continuation_ids": [],
    }
    winner_queue: list = []

    def _batch(claimed, decision_time, *, claim_unpaged_winner=None):
        observations["decision_times"].append(decision_time)
        observations["claimed_ids"].append(
            tuple(event.event_id for event in claimed)
        )
        batch_events = list(claimed)
        if not winner_queue:
            first = claimed[0]
            winner_queue.extend(
                event for event in events if event.event_id != first.event_id
            )
            winner = first
        elif winner_queue:
            target = winner_queue.pop(0)
            assert claim_unpaged_winner is not None
            winner = claim_unpaged_winner(target)
            assert winner is not None
            batch_events.append(winner)
        else:  # pragma: no cover - kept explicit for type narrowing
            raise AssertionError("unreachable")

        observations["winner_ids"].append(winner.event_id)
        receipts = {
            event.event_id: EventSubmissionReceipt(
                submitted=event.event_id == winner.event_id,
                event_id=event.event_id,
                causal_snapshot_id=event.causal_snapshot_id,
                side_effect_status=(
                    "VENUE_SUBMIT_ACKED"
                    if event.event_id == winner.event_id
                    else "NO_SUBMIT"
                ),
                venue_call_started=event.event_id == winner.event_id,
                venue_ack_received=event.event_id == winner.event_id,
                reason=(
                    "TEST_WINNER_SUBMITTED"
                    if event.event_id == winner.event_id
                    else f"GLOBAL_NOT_SELECTED:{winner.event_id}"
                ),
                proof_accepted=event.event_id == winner.event_id,
            )
            for event in batch_events
        }
        clock["value"] += 2.5
        if len(observations["winner_ids"]) == 3:
            # The third winner's requeued carrier performs one final complete
            # epoch. CASH/HOLD is then terminal and consumes the frontier.
            def _cash_batch(
                cash_claimed,
                cash_decision_time,
                *,
                claim_unpaged_winner=None,
            ):
                del claim_unpaged_winner
                observations["decision_times"].append(cash_decision_time)
                observations["claimed_ids"].append(
                    tuple(event.event_id for event in cash_claimed)
                )
                cash_receipts = {
                    event.event_id: EventSubmissionReceipt(
                        submitted=False,
                        event_id=event.event_id,
                        causal_snapshot_id=event.causal_snapshot_id,
                        reason=(
                            "GLOBAL_PREFLIGHT_HOLD_CASH_OPTIMAL:"
                            "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER"
                        ),
                        proof_accepted=False,
                    )
                    for event in cash_claimed
                }
                return GlobalBatchSubmitResult(
                    receipts=cash_receipts,
                    winner_event_id=None,
                    venue_submit_count=0,
                )

            reactor._submit.process_global_batch = _cash_batch
        continuation_event = make_opportunity_event(
            event_type=winner.event_type,
            entity_key=winner.entity_key,
            source=(
                "test_global_continuation:"
                f"{len(observations['winner_ids'])}:{winner.event_id}"
            ),
            observed_at=winner.observed_at,
            available_at=winner.available_at,
            received_at=decision_time.isoformat(),
            causal_snapshot_id=winner.causal_snapshot_id,
            payload=json.loads(winner.payload_json),
            priority=winner.priority,
            expires_at=winner.expires_at,
            created_at=decision_time.isoformat(),
        )
        observations["continuation_ids"].append(continuation_event.event_id)
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=winner.event_id,
            venue_submit_count=1,
            continuation_event=continuation_event,
        )

    reactor = _multiwinner_reactor(store, _batch)
    reactor._finalize_deferred_event_unit = _serialized_frontier_finalize(
        reactor
    )
    result = reactor.process_pending(decision_time=_DT_VENUE_OPEN, limit=1)

    assert observations["winner_ids"] == [event.event_id for event in events]
    assert observations["claimed_ids"] == [
        (events[0].event_id,),
        (observations["continuation_ids"][0],),
        (observations["continuation_ids"][1],),
        (observations["continuation_ids"][2],),
    ]
    decision_times = observations["decision_times"]
    assert decision_times == [
        _DT_VENUE_OPEN,
        _DT_VENUE_OPEN + timedelta(seconds=2.5),
        _DT_VENUE_OPEN + timedelta(seconds=5.0),
        _DT_VENUE_OPEN + timedelta(seconds=7.5),
    ]
    assert all(value.date() == _DT_VENUE_OPEN.date() for value in decision_times)
    assert result.proof_accepted == 3
    assert result.retried == 0
    assert all(
        _processing_status(conn, event.event_id) == "processed"
        for event in events
    )
