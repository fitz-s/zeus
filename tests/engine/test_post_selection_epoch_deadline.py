# Created: 2026-10-08
# Last audited: 2026-10-08
# Authority basis: live defect 2026-10-07 (228 winner preflights accepted=True, 3
#   venue commands): the 45 s construction work cut expired during winner
#   preflight and discarded winners whose book epoch was still current.
"""Stages that act on a frozen selection run to the book-epoch deadline.

Scope/prepare/book capture are bounded by the construction work cut. After the
book epoch is fenced, the selection receipt write, winner preflight and final
actuation are bounded by the epoch's own actuation deadline instead, and urgent
wake / exact held-SELL preemption (``cancel_requested``) still cancels them.

The construction budget is 2.0 s here and the slow stage sleeps 2.2 s, so each
test crosses the construction deadline for real while the epoch (60 s) stays
current. ``current_time_provider`` is fixed, so the epoch never expires on the
wall clock unless a test moves it.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json
import sqlite3
import time
from dataclasses import replace as dc_replace
from types import SimpleNamespace

import pytest

import src.engine.global_auction_universe as universe
import src.engine.global_batch_runtime as gbr
import src.state.decision_chain as decision_chain
from src.engine import tier0_auction_corpus as corpus
from src.events.reactor import EventSubmissionReceipt
from src.state.schema.tier0_auction_corpus_schema import ensure_tables
from tests.integration.test_w3_solve_seam_g3 import (
    _WealthNamespace,
    _global_scope_event,
    _global_test_buy_candidate,
    _global_test_candidate_book,
    current_global_auction_scope_from_events,
)

AT = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
_RUN_ID = itertools.count()
CONSTRUCT_BUDGET_S = 2.0
SLOW_S = 2.2
EPOCH_MAX_AGE = _dt.timedelta(seconds=60)


@pytest.fixture(autouse=True)
def _empty_queue():
    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()
    yield
    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()


def _stable():
    return gbr.GlobalWinnerPreflight(status="STABLE", binding_token="binding")


def _run(
    tmp_path,
    monkeypatch,
    *,
    preflight,
    receipt_stage=None,
    cancel_requested=None,
    wall_clock=lambda: AT,
    work_context=None,
):
    """One live-shaped cut: a single BUY winner on the preflighted lane.

    ``receipt_stage(persist)`` runs inside the selection-receipt writer with the
    cut's real artifact persister; its return value is the receipt row id.
    """

    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=AT)
    family_key = scope.family_keys[0]
    witness = SimpleNamespace(
        family_key=family_key,
        captured_at_utc=AT,
        posterior_identity_hash="run-a",
        witness_identity="q-run-a",
    )
    candidate = _global_test_buy_candidate(
        family_key=family_key,
        probability_witness_identity="q-run-a",
        book_identity="book-1",
        price="0.18",
        captured_at=AT,
        candidate_id="candidate-live",
    )
    book = dc_replace(
        _global_test_candidate_book(candidate, epoch_captured_at=AT),
        max_age=EPOCH_MAX_AGE,
    )
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=candidate, no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth-1",
            wealth_economic_identity="wealth-economics",
        ),
    )
    venue = [0]
    row_ids = iter(range(100, 200))
    trade = sqlite3.connect(tmp_path / f"trade-{next(_RUN_ID)}.db")
    ensure_tables(trade)
    trade.execute("CREATE TABLE receipt_probe (value TEXT NOT NULL)")
    trade.commit()
    monkeypatch.setattr(
        decision_chain,
        "store_artifact",
        lambda conn, _artifact: conn.execute(
            "INSERT INTO receipt_probe VALUES ('persisted')"
        ).lastrowid,
    )

    def store_receipt(conn, **kwargs):
        persisted = (
            receipt_stage(kwargs["persist_artifact"])
            if receipt_stage is not None
            else None
        )
        row_id = persisted or next(row_ids)
        cut = gbr._tier0_cut_corpus(
            selection_epoch_identity=kwargs["selection_epoch_identity"],
            reason=None,
            decision_at_utc=kwargs["decision_at_utc"],
            scope_family_count=1,
            probability_witnesses={},
            ineligible_by_family={},
            excluded_by_family={},
            evaluations=(),
            winner_candidate_id=candidate.candidate_id,
            book_epoch=None,
            family_context_by_key={},
            fractional_kelly_multiplier=kwargs["fractional_kelly_multiplier"],
            buy_candidates_enabled=True,
        )
        corpus.queue_cut(
            gbr._decision_log_connection_key(conn),
            corpus.PendingCut(lambda: (cut, ()), row_id),
        )
        return row_id

    monkeypatch.setattr(gbr, "scan_current_global_auction_scope", lambda **_: scope)
    monkeypatch.setattr(
        gbr,
        "current_portfolio_wealth_witness",
        lambda *_, **__: _WealthNamespace(
            spendable_cash_usd="10",
            witness_identity="wealth-1",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(gbr, "select_prepared_global_auction", lambda *_, **__: selected)
    monkeypatch.setattr(gbr, "_store_global_auction_receipt", store_receipt)
    monkeypatch.setattr(
        gbr, "_bind_stored_global_auction_receipt", lambda _c, **kw: kw["selected"]
    )
    monkeypatch.setattr(gbr, "_store_global_preflight_receipt", lambda *_, **__: 1)
    monkeypatch.setattr(
        gbr, "replace", lambda value, **changes: SimpleNamespace(**(vars(value) | changes))
    )

    def actuate(winner, _actuation, _at, _token, _authority):
        venue[0] += 1
        return EventSubmissionReceipt(
            True,
            winner.event_id,
            winner.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = gbr.process_current_global_batch(
        (event,),
        decision_time=AT,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=trade,
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=SimpleNamespace(probability_witness=witness),
        ),
        actuate_winner=lambda *_: pytest.fail("preflighted lane owns actuation"),
        preflight_winner=preflight,
        actuate_preflighted_winner=gbr.GlobalOneShotActuator(actuate),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue[0],
        current_execution=lambda *_: object(),
        current_time_provider=wall_clock,
        current_book_epoch_provider=lambda probabilities, _at, _ctx: (
            probabilities,
            book,
        ),
        work_context=work_context
        or universe.WorkContext(
            deadline_monotonic=time.monotonic() + CONSTRUCT_BUDGET_S,
            cancel_requested=cancel_requested,
        ),
    )
    persisted_rows = trade.execute("SELECT COUNT(*) FROM receipt_probe").fetchone()[0]
    trade.close()
    return result, event, venue[0], persisted_rows


def test_preflight_past_construct_deadline_inside_epoch_still_actuates(
    tmp_path, monkeypatch
):
    def slow_preflight(*_):
        time.sleep(SLOW_S)  # crosses the 2.0 s construction deadline
        return _stable()

    result, event, venue, _ = _run(tmp_path, monkeypatch, preflight=slow_preflight)

    assert result.venue_submit_count == 1 == venue
    assert result.winner_event_id == event.event_id
    assert result.receipts[event.event_id].submitted is True


def test_selection_receipt_written_past_construct_deadline_inside_epoch_persists(
    tmp_path, monkeypatch
):
    def slow_receipt(persist):
        time.sleep(SLOW_S)  # crosses the 2.0 s construction deadline
        return persist(object())

    result, event, venue, persisted = _run(
        tmp_path,
        monkeypatch,
        preflight=lambda *_: _stable(),
        receipt_stage=slow_receipt,
    )

    assert persisted == 1
    assert result.venue_submit_count == 1 == venue
    assert result.receipts[event.event_id].submitted is True


def test_preflight_past_the_book_epoch_deadline_is_refused(tmp_path, monkeypatch):
    wall = [AT]

    def preflight(*_):
        # The construction budget (2.0 s, real clock) is untouched; only the
        # book epoch's 60 s of wall time is spent.
        wall[0] = AT + EPOCH_MAX_AGE + _dt.timedelta(seconds=1)
        return _stable()

    result, event, venue, _ = _run(
        tmp_path, monkeypatch, preflight=preflight, wall_clock=lambda: wall[0]
    )

    assert venue == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == "GLOBAL_REAUCTION_EPOCH_EXPIRED"


def test_work_context_after_the_fence_expires_with_the_epoch_not_the_construct_cut(
    tmp_path, monkeypatch
):
    """Both clocks advance together: construct 45 s passes, epoch 60 s does not."""

    mono = [0.0]
    wall = [AT]

    def advance(seconds):
        mono[0] += seconds
        wall[0] = AT + _dt.timedelta(seconds=mono[0])

    def preflight(*_):
        advance(50.0)  # past the 45 s construct cut, inside the 60 s epoch
        return _stable()

    context = universe.WorkContext(deadline_monotonic=45.0, monotonic=lambda: mono[0])
    result, event, venue, _ = _run(
        tmp_path,
        monkeypatch,
        preflight=preflight,
        wall_clock=lambda: wall[0],
        work_context=context,
    )
    assert venue == 1
    assert result.receipts[event.event_id].submitted is True

    mono[0], wall[0] = 0.0, AT

    def too_slow(*_):
        advance(61.0)  # past the epoch
        return _stable()

    result, event, venue, _ = _run(
        tmp_path,
        monkeypatch,
        preflight=too_slow,
        wall_clock=lambda: wall[0],
        work_context=universe.WorkContext(
            deadline_monotonic=45.0, monotonic=lambda: mono[0]
        ),
    )
    assert venue == 0
    assert result.receipts[event.event_id].reason in {
        "DEFERRED_DEADLINE",
        "GLOBAL_REAUCTION_EPOCH_EXPIRED",
    }


@pytest.mark.parametrize("stage", ("preflight", "selection_receipt"))
def test_urgent_wake_during_post_selection_stage_still_cancels(
    tmp_path, monkeypatch, stage
):
    urgent = [False]

    def cancel():
        return "urgent_wake:test" if urgent[0] else False

    def preflight(*_):
        if stage == "preflight":
            urgent[0] = True
            time.sleep(0.1)
        return _stable()

    def receipt(persist):
        if stage == "selection_receipt":
            urgent[0] = True
        return persist(object())

    result, event, venue, persisted = _run(
        tmp_path,
        monkeypatch,
        preflight=preflight,
        receipt_stage=receipt if stage == "selection_receipt" else None,
        cancel_requested=cancel,
        work_context=universe.WorkContext(
            deadline_monotonic=time.monotonic() + 600.0, cancel_requested=cancel
        ),
    )

    assert venue == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == "DEFERRED_PREEMPTED"
    if stage == "selection_receipt":
        assert persisted == 0  # the write rolled back; cancel preempted it
