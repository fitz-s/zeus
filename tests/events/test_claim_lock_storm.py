# Created: 2026-06-11
# Last reused/audited: 2026-07-10
# Authority basis: operator URGENT 2026-06-11 17:51-17:56Z claim-storm incident.
#   Cycles alternated `processed=0 retried=250 reasons=[]` (whole cycle bounced) and
#   `processed=22 ... retried=209` (one mid-cycle bounce poisoned the rest). TWO
#   composed defects, both in-process:
#   (1) ROOT — main._edli_pending_entity_keys ran `PRAGMA busy_timeout = 250` on the
#       SHARED world conn (the EventStore claim conn) and never restored it: every
#       claim waited <=250 ms instead of the configured 30 s, so ANY overlapping
#       in-process writer (heartbeat 2 s, ingestor, wrap reconciler 30 s, user-channel
#       reconcile 60 s) bounced it.
#   (2) AMPLIFIER — the reactor's claim lock-error path returned WITHOUT rolling back,
#       leaving the implicit txn OPEN; the next fetch_pending then read INSIDE that
#       dangling txn (pinning a stale snapshot), and every later claim failed
#       SQLITE_BUSY_SNAPSHOT instantly (the busy handler never engages for
#       snapshot-upgrade conflicts) => the 0/250 storm. attempt_count never moved
#       (verified live: Busan stuck at 2) and the bounces were invisible (reasons=[]).
"""RELATIONSHIP tests across the boundary

    concurrent world writer txn -> EventStore.claim() under busy_timeout
    -> reactor Window A lock-error handling -> next fetch_pending's snapshot

Pins: (a) a claim contending with a held write txn WAITS and LANDS (no bounce);
(b) a dangling stale-snapshot txn cannot storm the cycle (guard rolls it back);
(c) a genuine claim bounce is VISIBLE (claim_lock_bounces + warning log) and
resets the conn (rollback), never silently folded into retried alone.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone

from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import OpportunityEventReactor, ReactorResult
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

_DT = datetime(2026, 6, 4, 18, 10, tzinfo=timezone.utc)


def _payload(snapshot_id: str) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-06-05",
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
        captured_at="2026-06-04T04:10:00+00:00",
        available_at="2026-06-04T04:15:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )


def _event(snapshot_id: str):
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-06-05|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-06-04T04:10:00+00:00",
        available_at="2026-06-04T04:15:00+00:00",
        received_at="2026-06-04T04:16:00+00:00",
        causal_snapshot_id=snapshot_id,
        payload=_payload(snapshot_id),
        priority=100,
    )


def _file_store(tmp_path):
    """File-backed world DB so a SECOND connection can contend for the WAL write
    lock (in-memory DBs cannot be shared across plain connections)."""
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    init_schema(conn)
    conn.commit()
    return db_path, conn, EventStore(conn)


def _reactor(conn, store) -> OpportunityEventReactor:
    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _d: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=lambda _event, _dt: None,  # terminal consume after gates
        reject=lambda *_a: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )


def _status(conn, event_id: str) -> str:
    return conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event_id,),
    ).fetchone()[0]


def test_claim_waits_for_concurrent_write_txn_and_lands(tmp_path):
    """ANTIBODY (a): a concurrent writer holding the WAL write lock does NOT bounce
    claim() — the busy handler engages (full configured timeout) and the claim
    lands once the writer commits. This is the test the live 250 ms downgrade
    would FAIL if it ever leaks back onto the claim conn."""
    db_path, conn, store = _file_store(tmp_path)
    event = _event("snap-contend")
    store.insert_or_ignore(event)
    conn.commit()

    lock_held = threading.Event()
    release_done = threading.Event()

    def _writer():
        other = sqlite3.connect(str(db_path), timeout=30.0)
        try:
            other.execute("PRAGMA busy_timeout = 30000")
            other.execute("BEGIN IMMEDIATE")  # takes the WAL write lock
            lock_held.set()
            time.sleep(0.6)  # hold well past any fast-bounce threshold
            other.commit()
            release_done.set()
        finally:
            other.close()

    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    assert lock_held.wait(5.0), "writer thread failed to take the write lock"

    reactor = _reactor(conn, store)
    result = reactor.process_pending(decision_time=_DT, limit=10)
    t.join(5.0)

    assert result.claim_lock_bounces == 0, (
        "claim bounced instead of waiting out the concurrent write txn — the busy "
        "handler is not engaged on the claim path (250ms-downgrade category)"
    )
    assert _status(conn, event.event_id) == "processed"
    assert release_done.is_set()


def test_claim_lock_contention_is_bounded_and_keeps_event_pending(tmp_path, monkeypatch):
    """Live cadence antibody: a pre-submit claim must not wait out the global
    30s busy_timeout. No order has been emitted yet, so a held WAL write lock is
    a fast retryable bounce and the event remains pending for the next cycle."""
    db_path, conn, store = _file_store(tmp_path)
    event = _event("snap-bounded")
    store.insert_or_ignore(event)
    conn.commit()
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "250")

    lock_held = threading.Event()
    release_writer = threading.Event()

    def _writer():
        other = sqlite3.connect(str(db_path), timeout=30.0)
        try:
            other.execute("PRAGMA busy_timeout = 30000")
            other.execute("BEGIN IMMEDIATE")
            lock_held.set()
            release_writer.wait(5.0)
            other.commit()
        finally:
            other.close()

    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    assert lock_held.wait(5.0), "writer thread failed to take the write lock"

    reactor = _reactor(conn, store)
    started = time.monotonic()
    result = reactor.process_pending(decision_time=_DT, limit=1)
    elapsed = time.monotonic() - started
    release_writer.set()
    t.join(5.0)

    assert elapsed < 1.5, f"claim lock bounce waited too long: {elapsed:.3f}s"
    assert result.claim_lock_bounces == 1
    assert result.retried == 1
    assert result.processed == 0 and result.rejected == 0 and result.dead_lettered == 0
    assert _status(conn, event.event_id) == "pending"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    assert not conn.in_transaction


def test_world_mutex_contention_before_claim_is_bounded_and_keeps_event_pending(
    tmp_path, monkeypatch
):
    """A process-local world mutex holder must not wedge the reactor before claim.

    No venue side effect has happened before Window A acquires the mutex, so a
    mutex miss is a retryable bounce. This pins the scheduler-liveness regression
    where an unbounded Python mutex wait could make one reactor cycle overrun the
    next APScheduler trigger.
    """
    from src.state.db import world_write_mutex

    _db_path, conn, store = _file_store(tmp_path)
    event = _event("snap-mutex-bounded")
    store.insert_or_ignore(event)
    conn.commit()
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "50")

    reactor = _reactor(conn, store)
    mutex = world_write_mutex()
    assert mutex.acquire(timeout=1.0)
    try:
        started = time.monotonic()
        result = reactor.process_pending(decision_time=_DT, limit=1)
        elapsed = time.monotonic() - started
    finally:
        mutex.release()

    assert elapsed < 0.5, f"world mutex bounce waited too long: {elapsed:.3f}s"
    assert result.claim_lock_bounces == 1
    assert result.retried == 1
    assert result.processed == 0 and result.rejected == 0 and result.dead_lettered == 0
    assert _status(conn, event.event_id) == "pending"
    assert not conn.in_transaction


def test_claim_contention_waits_once_then_probes_nonblocking(monkeypatch, caplog):
    import logging

    class _ContendedMutex:
        def __init__(self) -> None:
            self.timeouts: list[float] = []

        def acquire(self, *, timeout: float) -> bool:
            self.timeouts.append(timeout)
            return False

        def release(self) -> None:
            raise AssertionError("an unacquired mutex must not be released")

    mutex = _ContendedMutex()
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "250")
    monkeypatch.setattr("src.events.reactor.world_write_mutex", lambda: mutex)
    reactor = object.__new__(OpportunityEventReactor)
    result = ReactorResult()

    with caplog.at_level(logging.WARNING, logger="zeus.events.reactor"):
        for index in range(3):
            reactor._process_event_unit(
                _event(f"snap-probe-{index}"),
                decision_time=_DT,
                result=result,
            )

    assert mutex.timeouts == [0.25, 0.0, 0.0]
    assert result.claim_lock_bounces == 3
    assert result.retried == 3
    assert sum("claim mutex-bounce" in record.message for record in caplog.records) == 1


def test_successful_claim_probe_restores_normal_wait(monkeypatch):
    class _RecoveringMutex:
        def __init__(self) -> None:
            self.outcomes = iter((False, True, False))
            self.timeouts: list[float] = []
            self.releases = 0

        def acquire(self, *, timeout: float) -> bool:
            self.timeouts.append(timeout)
            return next(self.outcomes)

        def release(self) -> None:
            self.releases += 1

    class _Store:
        def __init__(self) -> None:
            self.conn = sqlite3.connect(":memory:")

        def claim(self, _event_id: str, *, claimed_at: str) -> bool:
            return True

    mutex = _RecoveringMutex()
    monkeypatch.setenv("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS", "250")
    monkeypatch.setattr("src.events.reactor.world_write_mutex", lambda: mutex)
    reactor = object.__new__(OpportunityEventReactor)
    reactor._store = _Store()
    reactor._process_one_pre_submit = lambda *_args, **_kwargs: (None, False)
    reactor._finalize_disposition = lambda *_args, **_kwargs: None
    result = ReactorResult()

    try:
        for index in range(3):
            reactor._process_event_unit(
                _event(f"snap-recover-{index}"),
                decision_time=_DT,
                result=result,
            )
    finally:
        reactor._store.conn.close()

    assert mutex.timeouts == [0.25, 0.0, 0.25]
    assert mutex.releases == 1
    assert result.claim_lock_bounces == 2
    assert result.retried == 2


def test_claim_contention_probe_state_resets_each_cycle(tmp_path):
    _db_path, conn, store = _file_store(tmp_path)
    reactor = _reactor(conn, store)
    reactor._claim_contention_seen = True

    try:
        reactor.process_pending(decision_time=_DT, limit=1)
    finally:
        conn.close()

    assert reactor._claim_contention_seen is False


def test_dangling_stale_snapshot_txn_cannot_storm_the_cycle(tmp_path):
    """ANTIBODY (b) — the storm reproducer: a dangling txn on the store conn with a
    PINNED READ SNAPSHOT, made stale by another writer's commit. On the old code
    every claim then failed SQLITE_BUSY_SNAPSHOT instantly (processed=0
    retried=N). The pre-fetch guard must roll the dangling txn back so the cycle
    proceeds normally."""
    db_path, conn, store = _file_store(tmp_path)
    event = _event("snap-stale")
    store.insert_or_ignore(event)
    conn.commit()

    # Recreate the EXACT poisoned state: open txn + read (snapshot pinned) ...
    conn.execute("BEGIN")
    conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()
    # ... then another connection commits a write => the pinned snapshot is stale.
    other = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        other.execute("PRAGMA busy_timeout = 30000")
        # Any committed write advances the WAL and stales the pinned snapshot —
        # a scratch table keeps the fixture independent of real-table constraints.
        other.execute("CREATE TABLE IF NOT EXISTS _stale_snapshot_marker (k TEXT)")
        other.execute("INSERT INTO _stale_snapshot_marker (k) VALUES ('x')")
        other.commit()
    finally:
        other.close()
    assert conn.in_transaction, "precondition: the dangling txn must be open"

    reactor = _reactor(conn, store)
    result = reactor.process_pending(decision_time=_DT, limit=10)

    assert result.claim_lock_bounces == 0, (
        "stale-snapshot dangling txn stormed the claims — the pre-fetch guard "
        "did not reset the conn"
    )
    assert _status(conn, event.event_id) == "processed"


def test_claim_lock_bounce_is_visible_and_resets_the_conn(tmp_path, caplog, monkeypatch):
    """ANTIBODY (c) — visibility + hygiene regression pin: a genuine claim lock
    error (1) increments claim_lock_bounces AND retried (never silent), (2) emits
    a warning log line, (3) ROLLS BACK so the conn carries no dangling txn into
    the next fetch, (4) leaves the event pending (re-claimable next cycle)."""
    import logging

    db_path, conn, store = _file_store(tmp_path)
    event = _event("snap-bounce")
    store.insert_or_ignore(event)
    conn.commit()

    def _locked_claim(_event_id, *, claimed_at=None):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "claim", _locked_claim)
    reactor = _reactor(conn, store)

    with caplog.at_level(logging.WARNING, logger="zeus.events.reactor"):
        result = reactor.process_pending(decision_time=_DT, limit=1)

    assert result.claim_lock_bounces == 1, "claim lock bounce must be counted, not silent"
    assert result.retried == 1
    assert result.processed == 0 and result.rejected == 0 and result.dead_lettered == 0
    assert any("claim lock-bounce" in rec.message for rec in caplog.records), (
        "claim lock bounce must emit a visible warning log line"
    )
    assert not conn.in_transaction, (
        "the lock-bounce path must ROLL BACK — a dangling txn here is the exact "
        "stale-snapshot storm amplifier"
    )
    assert _status(conn, event.event_id) == "pending"


def test_pending_entity_keys_restores_busy_timeout():
    """ANTIBODY (root cause): _edli_pending_entity_keys may downgrade busy_timeout
    for ITS OWN read, but the shared connection's configured value MUST be
    restored afterwards — the 250 ms leak onto the claim path was the storm's
    root cause."""
    from src.main import _edli_pending_entity_keys

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute("PRAGMA busy_timeout = 30000")

    _edli_pending_entity_keys(conn)

    restored = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert int(restored) == 30000, (
        f"busy_timeout leaked at {restored} ms after _edli_pending_entity_keys — "
        "the claim path inherits this connection-wide value (claim-storm root cause)"
    )


def test_pending_entity_keys_reads_only_newest_bounded_working_set():
    """The skip-set read must not scan the historical processing universe."""
    from src.main import _edli_pending_entity_keys

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn)
    old = _event("old")
    newest = _event("newest")
    store.insert_or_ignore(old)
    store.insert_or_ignore(newest)
    conn.execute(
        "UPDATE opportunity_event_processing SET updated_at = ? WHERE event_id = ?",
        ("2026-06-04T00:00:00+00:00", old.event_id),
    )
    conn.execute(
        "UPDATE opportunity_event_processing SET updated_at = ? WHERE event_id = ?",
        ("2026-06-04T01:00:00+00:00", newest.event_id),
    )
    conn.commit()
    traced: list[str] = []
    conn.set_trace_callback(traced.append)

    assert _edli_pending_entity_keys(conn, max_rows_per_status=1) == {
        newest.entity_key
    }
    conn.set_trace_callback(None)
    query = next(sql for sql in traced if "WITH active(event_id)" in sql)
    plan = "\n".join(
        str(row[3]) for row in conn.execute(f"EXPLAIN QUERY PLAN {query}").fetchall()
    )
    assert "MATERIALIZE active" in plan
    assert "sqlite_autoindex_opportunity_events_1 (event_id=?)" in plan


def test_pending_entity_keys_deadline_interrupts_and_clears_handler():
    """A slow skip-set read must yield; its progress handler must not leak."""
    from src.main import _edli_pending_entity_keys

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    store = EventStore(conn)
    for index in range(500):
        store.insert_or_ignore(_event(f"deadline-{index}"))
    conn.commit()

    keys = _edli_pending_entity_keys(
        conn,
        max_rows_per_status=500,
        deadline_monotonic=time.monotonic() - 1.0,
    )

    assert keys == set()
    assert conn.execute("SELECT 1").fetchone()[0] == 1
