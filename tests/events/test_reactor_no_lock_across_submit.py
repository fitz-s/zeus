# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: #95 SEV-2.1 — world_write_lock must NOT be held across network I/O.
#
# RELATIONSHIP TEST (cross-module invariant), per Fitz methodology:
#   "When the reactor's per-event world-DB write unit hands off to the injected
#    submit callable (which performs the JIT /book HTTP fetch in
#    main._edli_pre_submit_jit_book_quote_provider and the venue order POST in
#    executor), NO world-DB write lock may be held."
#
# The two observable proxies for "a world write lock is held":
#   1. world_write_mutex().locked() is True  (the process-global Python mutex
#      added 2026-05-31 to serialize reactor vs ingestor world writes).
#   2. store.conn.in_transaction is True      (an OPEN world-DB transaction holds
#      the SQLite WAL *write* lock once claim() has written — this is the actual
#      starvation root; the Python mutex is only the in-process proxy).
#
# self._submit is the EXACT seam where, in production wiring
# (event_reactor_adapter.event_bound_live_certificate_adapter_from_conns), the
# network calls live:
#   - pre_submit_authority_provider -> main._edli_pre_submit_jit_book_quote_provider
#     -> `with PolymarketClient() as clob: clob.get_orderbook_snapshot(...)`  (HTTP GET /book)
#   - executor_submit(final_intent, command)                                  (HTTP POST order)
# So asserting "the lock is not held when self._submit runs" pins the invariant
# at the module boundary without importing the venue stack.
from __future__ import annotations

from datetime import datetime, timezone

from src.events.event_store import EventStore
from src.events.reactor import EventSubmissionReceipt, OpportunityEventReactor, ReactorConfig
from src.state.db import init_schema, world_write_mutex
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

from tests.events.test_reactor import _forecast_event, _store


def _instrumented_reactor(store: EventStore):
    """Reactor whose injected submit callable RECORDS the lock state at the
    instant it is called — exactly when production performs network I/O."""
    observations: dict = {}

    def _submit(event, decision_time):
        # Capture both proxies for "a world write lock is held" at the network seam.
        observations["mutex_locked_at_submit"] = world_write_mutex().locked()
        observations["world_conn_in_txn_at_submit"] = bool(store.conn.in_transaction)
        # Return a fail-closed (unsubmitted) receipt so the post-submit world
        # ledger phase still executes and the event drains, without venue side effects.
        return EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            reason="LOCK_PROBE_NO_SUBMIT",
            proof_accepted=False,
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _decision_time: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(reactor_mode="live_no_submit"),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    return reactor, observations


def test_world_write_lock_not_held_across_network_submit():
    """RED on current code: the per-event world mutex (and the open world txn /
    WAL write lock opened by claim()) are held across self._submit, which in
    production wraps the JIT /book HTTP fetch and the venue order POST.

    GREEN after the SEV-2.1 fix: the world write unit commits (closing the WAL
    write lock) and releases the mutex BEFORE the network submit, then re-opens a
    fresh world txn for the post-submit ledger writes.
    """
    _conn, store = _store()
    store.insert_or_ignore(_forecast_event("lockprobe"))
    reactor, observations = _instrumented_reactor(store)

    reactor.process_pending(
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    )

    assert "mutex_locked_at_submit" in observations, (
        "submit callable was never invoked — the event did not reach the submit "
        "seam; the test cannot assert the invariant"
    )
    assert observations["mutex_locked_at_submit"] is False, (
        "world_write_mutex was HELD while the (network) submit callable ran — "
        "this serializes all world writes behind the JIT /book fetch + venue POST "
        "and re-introduces SQLite WAL lock starvation (#95 SEV-2.1)"
    )
    assert observations["world_conn_in_txn_at_submit"] is False, (
        "an OPEN world-DB transaction (SQLite WAL write lock, opened by claim()) "
        "was held while the network submit ran — the WAL write lock across HTTP "
        "is the actual starvation root, independent of the Python mutex (#95 SEV-2.1)"
    )
