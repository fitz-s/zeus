# Created: 2026-10-09
# Last reused/audited: 2026-10-09
# Authority basis: finite_evidence_probability_symmetry/PLAN.md; INV-47.
"""Real SQLite contention, controlled sub-ms deadline, real reactor ownership.

The test clock controls only scheduling at the error boundary. SQLite acquires
the exclusive lock and produces every BUSY exception itself. No SQL is replayed.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.engine import global_auction_universe as universe
from src.engine import global_batch_runtime as batch
from src.events import reactor as reactor_module
from src.events.event_store import EventStore
from src.events.reactor import OpportunityEventReactor
from src.runtime.reactor_wake import event_rows_state
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger
from tests.integration.test_w3_solve_seam_g3 import _global_scope_event
from tests.integration.test_w3_solve_seam_g3 import (  # noqa: F401
    _hko_clock_native_sources,
    _normal_hko_concentrated_sell,
)


@pytest.fixture(autouse=True)
def isolated_cut_queue():
    from src.engine import tier0_auction_corpus as corpus

    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()
    yield
    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()


class BoundaryClock:
    """Hold the watcher before expiry; advance the owner after the BUSY cut."""

    deadline = 10.0509

    def __init__(self, *, remaining=0.0005, advance=True):
        self.owner = threading.get_ident()
        self.remaining = remaining
        self.advance = advance
        self.error = None
        self.samples = []
        self.failed_statements = []
        self.interrupted = threading.Event()

    def __call__(self):
        if self.error is None or threading.get_ident() != self.owner:
            return 10.0
        value = self.deadline - self.remaining
        if self.samples and self.advance:
            value = self.deadline
        self.samples.append(value)
        return value


class ObservedConnection(sqlite3.Connection):
    clock: BoundaryClock

    def execute(self, sql, *args, **kwargs):
        try:
            return super().execute(sql, *args, **kwargs)
        except sqlite3.OperationalError as error:
            self.clock.error = error
            self.clock.failed_statements.append(sql)
            raise

    def interrupt(self):
        super().interrupt()
        self.clock.interrupted.set()


@pytest.fixture
def locked_selection(tmp_path):
    path = tmp_path / "selection.db"
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE readiness_state (value TEXT NOT NULL)")
    seed.commit()
    seed.close()
    owner = sqlite3.connect(path)
    selection = sqlite3.connect(path, factory=ObservedConnection)
    selection.clock = BoundaryClock()
    owner.execute("BEGIN EXCLUSIVE")
    owner.execute("CREATE TABLE held_schema_lock (value TEXT)")
    try:
        yield owner, selection, path
    finally:
        owner.rollback()
        selection.close()
        owner.close()


@pytest.mark.parametrize("shared", [False, True])
def test_real_busy_truncation_defers_with_original_cause(
    locked_selection, monkeypatch, shared, record_property,
):
    owner, selection, path = locked_selection
    clock = selection.clock
    opened = []

    def open_read(_path):
        assert _path == path
        conn = sqlite3.connect(path, factory=ObservedConnection)
        conn.clock = clock
        opened.append(conn)
        return conn

    monkeypatch.setattr(universe, "_connect_read_only", open_read)
    context = universe.WorkContext(clock.deadline, monotonic=clock)
    original_timeout = selection.execute("PRAGMA busy_timeout").fetchone()[0]
    with pytest.raises(universe.WorkDeferred) as raised:
        with universe.bounded_work_sqlite(
            selection, context, stage="selection_snapshot", shared_connection=shared,
        ) as read:
            read.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    assert owner.in_transaction
    assert raised.value.code is universe.WorkDeferredCode.DEADLINE
    assert raised.value.__cause__ is clock.error
    assert clock.error.sqlite_errorcode == sqlite3.SQLITE_BUSY
    assert clock.error.sqlite_errorname == "SQLITE_BUSY"
    assert clock.samples[0] < clock.deadline <= clock.samples[-1]
    assert context.deadline_monotonic == 10.0509
    assert clock.failed_statements == ["SELECT 1 FROM sqlite_master LIMIT 1"]
    assert selection.execute("PRAGMA busy_timeout").fetchone()[0] == original_timeout
    assert selection.in_transaction is False
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")
    record_property("sqlite_error", clock.error.sqlite_errorname)
    record_property("boundary_clock", json.dumps(clock.samples))


def _blocked_batch(events, at, selection):
    return batch.process_current_global_batch(
        events, decision_time=at, world_conn=object(), forecast_conn=object(),
        trade_conn=object(), payload_reader=lambda e: json.loads(e.payload_json),
        prepare_event=lambda *_: pytest.fail("locked cut must not prepare"),
        actuate_winner=lambda *_: pytest.fail("locked cut must not actuate"),
        stamp_receipt=lambda receipt: receipt, venue_submit_count=lambda: 0,
        current_execution=lambda *_: pytest.fail("locked cut must not authorize"),
        current_time_provider=lambda: pytest.fail("locked cut must not name a cut"),
        current_book_epoch_provider=lambda *_: pytest.fail("locked cut must not fetch books"),
        work_context=universe.WorkContext(selection.clock.deadline, monotonic=selection.clock),
        selection_snapshot_connections=(selection,),
    )


def _reactor(conn, event, process_batch):
    store = EventStore(conn)
    store.insert_or_ignore(event)

    def submit(*_):
        pytest.fail("global path must own the cut")

    submit.process_global_batch = process_batch
    reactor = OpportunityEventReactor(
        store, source_truth_gate=lambda _: True,
        executable_snapshot_gate=lambda *_: True, riskguard_gate=lambda _: True,
        final_intent_submit=submit, reject=lambda *_: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )
    conn.commit()
    return reactor


def _processing(conn, event):
    return conn.execute(
        "SELECT event_id, processing_status, claimed_at, attempt_count, last_error "
        "FROM opportunity_event_processing WHERE event_id = ?", (event.event_id,),
    ).fetchone()


def test_real_batch_busy_requeues_without_completing_wake(
    locked_selection, record_property,
):
    owner, selection, _ = locked_selection
    at = datetime(2026, 7, 10, 8, tzinfo=timezone.utc)
    event = _global_scope_event(city="Chicago", source_run_id="run-a")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    batches = []

    def process(events, when, **_):
        result = _blocked_batch(events, when, selection)
        batches.append(result)
        return result

    reactor = _reactor(conn, event, process)
    prior_debt = reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set()
    reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
    try:
        assert reactor._store.fetch_pending(decision_time=at.isoformat(), limit=1), tuple(_processing(conn, event))
        conn.commit()
        result = reactor.process_pending(decision_time=at, limit=1)
        row = _processing(conn, event)
        assert result.retried == 1 and result.processed == result.dead_lettered == 0
        assert result.proof_accepted == batches[0].venue_submit_count == 0
        assert row[1:4] == ("pending", None, 1)
        assert event_rows_state((tuple(row[:3]),)) == (False, False)
        assert not batches[0].economic_cut_completed
        assert not result.global_auction_completed_non_cancelled
        assert batches[0].held_sell_completion_cut is None
        assert not reactor_module._settle_global_auction_monitor_fairness(
            completion_due_at_start=True, result=result,
        )
        assert reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set()
        assert owner.in_transaction and not selection.in_transaction
        record_property("queue_row", json.dumps(tuple(row)))
        # Last on purpose: the RED still establishes honest inherited retry and
        # retained debt before proving the incorrect raw-failure classification.
        assert row[4] == "DEFERRED_DEADLINE"
    finally:
        if not prior_debt:
            reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
        conn.close()


@pytest.mark.parametrize("case", ["early_busy", "shorter_owner_timeout", "unlimited"])
def test_real_busy_outside_deadline_clip_is_unchanged(locked_selection, case):
    _, conn, _ = locked_selection
    clock = BoundaryClock(remaining=0.010 if case == "early_busy" else 0.0005, advance=False)
    conn.clock = clock
    if case != "early_busy":
        conn.execute("PRAGMA busy_timeout = 5")
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    context = universe.WorkContext(None if case == "unlimited" else clock.deadline, monotonic=clock)
    with pytest.raises(sqlite3.OperationalError) as raised:
        with universe.bounded_work_sqlite(conn, context, stage="control", shared_connection=True):
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    assert raised.value is clock.error
    assert raised.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
    assert raised.value.__cause__ is None
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == timeout


@pytest.mark.parametrize("kind", ["missing_table", "locked", "busy_snapshot", "interrupt"])
def test_other_sqlite_errors_keep_exact_native_exception(tmp_path, kind):
    conn = sqlite3.connect(tmp_path / "error-control.db", factory=ObservedConnection)
    conn.clock = BoundaryClock(advance=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE sample (value INTEGER)")
    conn.executemany("INSERT INTO sample VALUES (?)", [(1,), (2,)])
    conn.commit()
    other = sqlite3.connect(tmp_path / "error-control.db")
    cursor = None
    try:
        if kind == "busy_snapshot":
            conn.execute("BEGIN")
            conn.execute("SELECT * FROM sample").fetchall()
            other.execute("INSERT INTO sample VALUES (3)")
            other.commit()
            sql, code = "UPDATE sample SET value = 4", sqlite3.SQLITE_BUSY_SNAPSHOT
        elif kind == "locked":
            cursor = conn.execute("SELECT * FROM sample")
            cursor.fetchone()
            sql, code = "DROP TABLE sample", sqlite3.SQLITE_LOCKED
        elif kind == "interrupt":
            conn.set_progress_handler(lambda: 1, 1000)
            sql, code = "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n", sqlite3.SQLITE_INTERRUPT
        else:
            sql, code = "SELECT * FROM missing_table", sqlite3.SQLITE_ERROR
        context = universe.WorkContext(conn.clock.deadline, monotonic=conn.clock)
        with pytest.raises(sqlite3.OperationalError) as raised:
            with universe.bounded_work_sqlite(conn, context, stage="control", shared_connection=True):
                conn.execute(sql)
        assert raised.value is conn.clock.error
        assert raised.value.sqlite_errorcode == code
        assert raised.value.__cause__ is None
        conn.set_progress_handler(None, 0)
        if cursor is not None:
            cursor.close()
        conn.rollback()
        assert conn.execute("SELECT value FROM sample ORDER BY value").fetchall() == (
            [(1,), (2,), (3,)] if kind == "busy_snapshot" else [(1,), (2,)]
        )
    finally:
        other.close()
        conn.close()


def test_cancellation_during_deadline_remainder_keeps_source_and_cause(locked_selection):
    _, conn, _ = locked_selection
    clock = conn.clock
    context = universe.WorkContext(
        clock.deadline, monotonic=clock,
        cancel_requested=lambda: "held_monitor" if clock.samples else False,
    )
    with pytest.raises(universe.WorkDeferred) as raised:
        with universe.bounded_work_sqlite(conn, context, stage="control", shared_connection=True):
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    assert raised.value.code is universe.WorkDeferredCode.PREEMPTED
    assert raised.value.source == "held_monitor"
    assert raised.value.__cause__ is clock.error


def test_watcher_cancellation_remembered_during_remainder_wins_deadline(
    locked_selection, monkeypatch,
):
    _, conn, _ = locked_selection
    clock = conn.clock
    sleep_started = threading.Event()
    consumed = []

    def one_shot_cancel():
        if threading.get_ident() != clock.owner and sleep_started.is_set() and not consumed:
            consumed.append(True)
            return "one_shot_held_monitor"
        return False

    def interleave_watcher(seconds):
        assert 0 < seconds < .001
        sleep_started.set()
        assert clock.interrupted.wait(1)

    monkeypatch.setattr(universe, "_time", SimpleNamespace(sleep=interleave_watcher))
    context = universe.WorkContext(clock.deadline, monotonic=clock, cancel_requested=one_shot_cancel)
    with pytest.raises(universe.WorkDeferred) as raised:
        with universe.bounded_work_sqlite(conn, context, stage="control", shared_connection=True):
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    assert consumed == [True]
    assert raised.value.code is universe.WorkDeferredCode.PREEMPTED
    assert raised.value.source == "one_shot_held_monitor"
    assert raised.value.__cause__ is clock.error


@pytest.mark.parametrize("bid", [".30", ".50"])
def test_normal_retry_rebuilds_source_cut_inside_executable_window(
    _normal_hko_concentrated_sell, locked_selection, monkeypatch, bid, record_property,
):
    from decimal import Decimal
    from tests.engine.test_physical_exit_window_acceptance import _current_source_cut

    case = _normal_hko_concentrated_sell
    owner, selection, _ = locked_selection
    at, event = case.fixture.cut, case.event
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    batches, retries = [], []

    def process(events, when, **_):
        assert tuple(e.event_id for e in events) == (event.event_id,)
        if owner.in_transaction:
            result = _blocked_batch(events, when, selection)
        else:
            actual = batch.process_current_global_batch

            def fresh_cut(events, **kwargs):
                kwargs["selection_snapshot_connections"] = (selection,)
                kwargs["work_context"] = universe.WorkContext(time.monotonic() + 30)
                book = kwargs["current_book_epoch_provider"]
                kwargs["current_book_epoch_provider"] = lambda probabilities, cut, context: book(probabilities, cut)
                return actual(events, **kwargs)

            with monkeypatch.context() as retry:
                retry.setattr(batch, "process_current_global_batch", fresh_cut)
                cut = _current_source_cut(case, event=events[0], at=when, monkeypatch=retry, bid=bid)
            retries.append(cut)
            result = cut.batch
        batches.append(result)
        return result

    reactor = _reactor(conn, event, process)
    prior_debt = reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set()
    reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.set()
    try:
        deferred = reactor.process_pending(decision_time=at, limit=1)
        assert deferred.retried == 1 and deferred.processed == 0
        assert _processing(conn, event)[1:5] == ("pending", None, 1, "DEFERRED_DEADLINE")
        assert not reactor_module._settle_global_auction_monitor_fairness(
            completion_due_at_start=True, result=deferred,
        )
        assert not retries
        owner.rollback()
        retried = reactor.process_pending(decision_time=at, limit=1)
        assert len(retries) == 1 and len(batches) == 2
        assert _processing(conn, event)[3] == 2
        cut = retries[0]
        assert 0 < case.q < .50
        assert cut.probability.captured_at_utc == at
        assert cut.curve.quote_ttl.total_seconds() == 30
        assert not selection.in_transaction
        assert case.trade.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
        if bid == ".30":
            assert cut.selection.decision.candidate is None
            assert cut.actuations == []
            assert retried.global_auction_completed_non_cancelled == 1
            assert retried.processed == 1 and retried.retried == 0
            assert reactor_module._settle_global_auction_monitor_fairness(
                completion_due_at_start=True, result=retried,
            )
            assert not reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set()
        else:
            decision = cut.selection.decision
            assert decision.candidate.action == "SELL"
            assert decision.candidate.execution_mode == "TAKER_LIMIT"
            assert Decimal(".05") <= cut.curve.levels[0].price <= Decimal(".95")
            assert decision.expected_terminal_wealth.expected_ev_usd > 0
            assert decision.expected_terminal_wealth.expected_delta_log_wealth > 0
            assert cut.actuations
            # The existing harness stops at preflight; selection is not a
            # submitted fill or a completed HOLD/CASH cut.
            assert not retried.global_auction_completed_non_cancelled
            assert retried.retried == 1 and retried.processed == 0
            assert not reactor_module._settle_global_auction_monitor_fairness(
                completion_due_at_start=True, result=retried,
            )
            assert reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.is_set()
        record_property("normal_retry", json.dumps({
            "metric": case.position.temperature_metric, "bid": bid, "q": case.q,
            "attempt_count": _processing(conn, event)[3],
            "economic_cut_completed": batches[-1].economic_cut_completed,
            "reason": batches[-1].receipts[event.event_id].reason,
        }))
    finally:
        if not prior_debt:
            reactor_module._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
        conn.close()
