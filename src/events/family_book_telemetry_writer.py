# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO (BLOCKER 1: synchronous JSON+SQLite on the
#   live decision thread cannot bound latency -- an exception handler cannot
#   undo elapsed WAL-writer-lock wait time; the repository's default busy
#   timeout is 30s and SQLite WAL permits only one writer at a time) plus the
#   team-lead follow-up research (INV-37 scope confirmed via
#   architecture/invariants.yaml:882-897; db_writer_lock/WriteClass wiring;
#   canonical counters sink; queue/worker shape modeled on
#   src/data/replacement_cycle_advance_trigger.py's day0-bridge pattern).
"""Nonblocking capture plane for family_book_states / family_book_observations.

The decision thread calls ONLY ``enqueue_family_book_observation`` -- a bounded
``queue.put_nowait`` of a small envelope of already-in-memory object
references. Zero serialization, zero SQLite, zero blocking; a full queue
increments a drop counter and returns immediately. All manifest-building,
hashing, JSON serialization, sampling-policy evaluation, and SQLite I/O happen
on a separate owner-local writer thread that opens its OWN trade-DB connection
with a short busy_timeout (``get_trade_connection(busy_timeout_ms=...)`` --
the repo's sanctioned "optional derived publication yields to live writers"
carve-out; see src/state/db.py ``_connect`` docstring).

INV-37 (architecture/invariants.yaml:882-897): "No Zeus write transaction may
span more than one physical DB via independent connections." This writer's
connection touches ONLY zeus_trades.db, never world/forecasts in the same
transaction -- outside INV-37's scope as written, confirmed by reading the
invariant text directly (not assumed).

Writer-lock arbitration (first production caller of
src/state/db_writer_lock.py's ``db_writer_lock`` -- Phase 0 landed the helper,
no caller existed until this): each batch commit is wrapped in
``db_writer_lock(trade_db_path, WriteClass.BULK, blocking=False)``. BULK is
correct because telemetry must always yield, never contend to win -- see
"Writer-class rationale" below for why this does not (yet) provide direct
arbitration against the primary trade_conn specifically.

Sampling policy v1 (replaces the broken per-cycle-unique timestamped hash as
the volume control): append on book-content STATE_CHANGE, on a HEARTBEAT
every ``_HEARTBEAT_INTERVAL`` while unchanged, or on any DECISION where a
trade was actually selected. Everything else is sampled out (counted, not
written) -- this is the actual, correct volume control; family_book_states
dedup (content_hash) controls STATE row volume, this policy controls
OBSERVATION row volume.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Sequence

from src.events.family_book_manifest import (
    build_manifest,
    compute_state_identity,
    market_center_native,
    market_center_status,
    market_q_fields,
    model_q_fields,
)
from src.events.idempotency import sha256_text
from src.observability.counters import increment as _cnt_inc
from src.observability.counters import read as _cnt_read
from src.state.db_writer_lock import WriteClass, db_writer_lock
from src.state.schema.family_book_observations_schema import (
    MARKET_CENTER_VERSION,
    SAMPLING_POLICY_VERSION,
    SCHEMA_VERSION as _OBSERVATIONS_SCHEMA_VERSION,
    ensure_table as _ensure_observations_table,
    insert_observation,
)
from src.state.schema.family_book_states_schema import (
    ensure_table as _ensure_states_table,
    insert_state,
)

logger = logging.getLogger(__name__)

_QUEUE_MAXSIZE_DEFAULT = 2048
_HEARTBEAT_INTERVAL = timedelta(minutes=30)
# Optional/derived publication yields to live writers (src/state/db.py
# _connect's sanctioned short-budget carve-out); this is NOT the live
# 30-second default.
_WRITER_BUSY_TIMEOUT_MS = 250
_LOG_RATE_LIMIT_SECONDS = 60.0
# SQLite's multi-connection WAL-reset fix landed in 3.51.3 (and backports
# 3.50.7/3.44.6) -- asserted once at worker startup per the deep review,
# since this module is the first thing in the repo to run a SECOND live
# writer connection against the trade DB concurrently with the primary.
_MIN_SQLITE_VERSION_INFO = (3, 51, 3)

# Canonical counter names (src/observability/counters.py sink).
_CNT_ENQUEUED = "family_book_telemetry_enqueued_total"
_CNT_DROP = "telemetry_drop_total"
_CNT_ENQUEUE_ERROR = "family_book_telemetry_enqueue_error_total"
_CNT_QUEUE_HIGH_WATER = "telemetry_queue_high_water_total"
_CNT_SAMPLED_OUT = "family_book_telemetry_sampled_out_total"
_CNT_WRITE_FAILURES = "family_book_telemetry_write_failures_total"
_CNT_WRITE_CONTENDED = "family_book_telemetry_write_contended_total"
_CNT_WRITTEN_STATES = "family_book_telemetry_written_states_total"
_CNT_WRITTEN_OBSERVATIONS = "family_book_telemetry_written_observations_total"

# Sentinel pushed onto the queue to stop the worker -- matches the
# day0-materialization-bridge shape (src/data/replacement_cycle_advance_trigger.py
# _DAY0_BRIDGE_STOP): the worker blocks on queue.get() with no poll timeout,
# so shutdown is immediate (no up-to-500ms poll latency) rather than a
# threading.Event checked on a timed loop.
_STOP = object()


@dataclass(frozen=True)
class _ObservationEnvelope:
    """Immutable references only -- no copying, no serialization at enqueue time."""

    decision: Any  # FamilyDecision
    family: Any  # EventBoundCandidateFamily
    active_proofs: tuple
    candidate_bin_id: Callable[[Any], str]
    decision_time: datetime
    causal_snapshot_id: Optional[str]


_queue_lock = threading.Lock()
_obs_queue: "queue.Queue[object]" = queue.Queue(maxsize=_QUEUE_MAXSIZE_DEFAULT)
_queue_high_water = 0  # raw gauge (not a counters.py monotonic sink value)
_worker_thread: Optional[threading.Thread] = None
_worker_started_lock = threading.Lock()
_last_state_by_family: dict[str, tuple[str, datetime]] = {}
_last_log_monotonic = 0.0


def _default_conn_factory() -> sqlite3.Connection:
    from src.state.db import get_trade_connection

    return get_trade_connection(busy_timeout_ms=_WRITER_BUSY_TIMEOUT_MS)


def _default_trade_db_path():
    from src.state.db import _zeus_trade_db_path

    return _zeus_trade_db_path()


_conn_factory: Callable[[], sqlite3.Connection] = _default_conn_factory
_trade_db_path_factory: Callable[[], Any] = _default_trade_db_path


def queue_high_water() -> int:
    """Peak queue occupancy observed since the last reset (test/ops introspection)."""
    return _queue_high_water


def enqueue_family_book_observation(
    *,
    decision: Any,
    family: Any,
    active_proofs: Sequence[Any],
    candidate_bin_id: Callable[[Any], str],
    decision_time: datetime,
    causal_snapshot_id: Optional[str],
) -> None:
    """Bounded, nonblocking enqueue -- call ONLY from the live decision thread.

    No serialization, no SQLite, no blocking wait: ``queue.put_nowait`` either
    succeeds immediately or raises ``queue.Full``, which increments a drop
    counter and returns. Any other exception is swallowed the same way --
    telemetry must never affect the decision it is called from.
    """
    global _queue_high_water
    try:
        if decision is None or decision.family_book is None:
            return
        _ensure_worker_started()
        envelope = _ObservationEnvelope(
            decision=decision,
            family=family,
            active_proofs=tuple(active_proofs),
            candidate_bin_id=candidate_bin_id,
            decision_time=decision_time,
            causal_snapshot_id=causal_snapshot_id,
        )
        _obs_queue.put_nowait(envelope)
        _cnt_inc(_CNT_ENQUEUED)
        size = _obs_queue.qsize()
        if size > _queue_high_water:
            _queue_high_water = size
            _cnt_inc(_CNT_QUEUE_HIGH_WATER)
    except queue.Full:
        _cnt_inc(_CNT_DROP)
    except Exception:  # noqa: BLE001 -- must never affect the decision thread
        _cnt_inc(_CNT_ENQUEUE_ERROR)


def start_worker(
    *,
    conn_factory: Optional[Callable[[], sqlite3.Connection]] = None,
    trade_db_path_factory: Optional[Callable[[], Any]] = None,
    maxsize: Optional[int] = None,
) -> None:
    """Explicitly (re)start the writer thread. Idempotent if already running
    with no override. Tests pass ``conn_factory``/``trade_db_path_factory``
    pointing at an isolated file-backed DB; production leaves all args unset
    (real trade DB)."""
    global _conn_factory, _trade_db_path_factory
    if conn_factory is not None:
        _conn_factory = conn_factory
    if trade_db_path_factory is not None:
        _trade_db_path_factory = trade_db_path_factory
    _stop_current_worker()
    if maxsize is not None:
        _configure_queue(maxsize)
    _ensure_worker_started()


def _configure_queue(maxsize: int) -> None:
    global _obs_queue
    with _queue_lock:
        _obs_queue = queue.Queue(maxsize=maxsize)


def _stop_current_worker(timeout: float = 5.0) -> None:
    """Stop whichever worker is currently reading the CURRENT ``_obs_queue``,
    using that exact queue object -- reassigning ``_obs_queue`` (via
    ``_configure_queue``) before stopping would orphan a running worker
    blocked on the old queue's ``get()`` forever."""
    global _worker_thread
    thread = _worker_thread
    if thread is None or not thread.is_alive():
        _worker_thread = None
        return
    _obs_queue.put(_STOP)  # blocking put: control-plane, not the decision path
    thread.join(timeout=timeout)
    _worker_thread = None


def _ensure_worker_started() -> None:
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    with _worker_started_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        thread = threading.Thread(
            target=_worker_loop, name="family-book-telemetry-writer", daemon=True
        )
        _worker_thread = thread
        thread.start()


def shutdown(timeout: float = 5.0) -> None:
    """Stop the writer thread (test/ops lifecycle helper)."""
    _stop_current_worker(timeout=timeout)


def drain(timeout: float = 5.0) -> bool:
    """Block until every item enqueued so far has been processed, or timeout.

    Test/ops synchronization helper -- never called from the decision thread.
    Returns True iff the queue fully drained within ``timeout``.
    """
    done = threading.Event()
    current_queue = _obs_queue

    def _joiner() -> None:
        current_queue.join()
        done.set()

    threading.Thread(target=_joiner, daemon=True).start()
    return done.wait(timeout=timeout)


def reset_for_test() -> None:
    """Full reset: stop worker, clear queue/sampling cache, restore default
    factories, reset the canonical counters sink. Test-only (``reset_all()``
    is documented in src/observability/counters.py as test-support-only)."""
    global _conn_factory, _trade_db_path_factory, _queue_high_water
    _stop_current_worker()
    _conn_factory = _default_conn_factory
    _trade_db_path_factory = _default_trade_db_path
    _configure_queue(_QUEUE_MAXSIZE_DEFAULT)
    _last_state_by_family.clear()
    _queue_high_water = 0
    from src.observability.counters import reset_all

    reset_all()


def counter(name: str) -> int:
    """Read back a named counter via the canonical sink (test/ops helper)."""
    return _cnt_read(name)


def _worker_loop() -> None:
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION_INFO:
        logger.error(
            "family_book_telemetry: refusing to start a second trade-DB writer "
            "connection -- sqlite3 %s is below the multi-connection WAL-reset "
            "fix floor %s",
            sqlite3.sqlite_version, _MIN_SQLITE_VERSION_INFO,
        )
        return
    try:
        conn = _conn_factory()
    except Exception:
        logger.warning("family_book_telemetry: writer thread failed to connect", exc_info=True)
        return
    try:
        _ensure_states_table(conn)
        _ensure_observations_table(conn)
    except Exception:
        logger.warning("family_book_telemetry: schema init failed", exc_info=True)
        conn.close()
        return
    trade_db_path = _trade_db_path_factory()
    try:
        while True:
            item = _obs_queue.get()
            try:
                if item is _STOP:
                    return
                _process_one(conn, trade_db_path, item)
            finally:
                _obs_queue.task_done()
    finally:
        conn.close()


def _rate_limited_warning(msg: str) -> None:
    global _last_log_monotonic
    now = time.monotonic()
    if now - _last_log_monotonic >= _LOG_RATE_LIMIT_SECONDS:
        _last_log_monotonic = now
        logger.warning(msg, exc_info=True)


def _process_one(conn: sqlite3.Connection, trade_db_path: Any, envelope: _ObservationEnvelope) -> None:
    try:
        # Writer-class rationale: BULK always yields, never contends to win.
        # The primary trade_conn does not YET take WriteClass.LIVE (Phase 0
        # of db_writer_lock landed the helper with no production caller --
        # this is the first). Taking BULK here therefore does not yet
        # arbitrate against the primary specifically; it (a) correctly
        # self-classifies this writer as non-critical/yielding so a future
        # LIVE retrofit of the primary path needs zero changes on this side,
        # and (b) prevents two instances of THIS writer (or any other
        # future BULK-classified caller) from writing concurrently.
        # Non-blocking: contention is a normal, expected, benign event for
        # best-effort telemetry -- skip this observation, never wait.
        with db_writer_lock(trade_db_path, WriteClass.BULK, blocking=False):
            _write_observation(conn, envelope)
    except BlockingIOError:
        _cnt_inc(_CNT_WRITE_CONTENDED)
    except Exception:
        _cnt_inc(_CNT_WRITE_FAILURES)
        _rate_limited_warning("family_book_telemetry: observation write failed")


def _sampling_reason(
    family_id: str, state_id: str, decision_time: datetime, decision: Any
) -> Optional[str]:
    """STATE_CHANGE | HEARTBEAT | DECISION, or None (sampled out -- not written)."""
    last = _last_state_by_family.get(family_id)
    if last is None or state_id != last[0]:
        return "STATE_CHANGE"
    if decision_time - last[1] >= _HEARTBEAT_INTERVAL:
        return "HEARTBEAT"
    if decision.selected is not None:
        return "DECISION"
    return None


def _write_observation(conn: sqlite3.Connection, envelope: _ObservationEnvelope) -> None:
    decision = envelope.decision
    family = envelope.family
    family_book = decision.family_book

    manifest = build_manifest(
        decision, active_proofs=envelope.active_proofs, candidate_bin_id=envelope.candidate_bin_id
    )
    state_id, content_hash, canonical_payload = compute_state_identity(
        family_id=family.family_id,
        topology_hash=decision.omega.topology_hash,
        complete_book=family_book.complete_book,
        manifest=manifest,
    )
    decision_time_iso = envelope.decision_time.isoformat()

    sampling_reason = _sampling_reason(family.family_id, state_id, envelope.decision_time, decision)
    if sampling_reason is None:
        _cnt_inc(_CNT_SAMPLED_OUT)
        return

    if insert_state(
        conn,
        state_id=state_id,
        family_id=family.family_id,
        content_hash=content_hash,
        topology_hash=decision.omega.topology_hash,
        complete_book=family_book.complete_book,
        canonical_payload=canonical_payload,
        first_seen_decision_time=decision_time_iso,
    ):
        _cnt_inc(_CNT_WRITTEN_STATES)

    model_q_json, model_q_identity_hash = model_q_fields(decision)
    row = {
        "observation_id": sha256_text(
            f"{family.family_id}|{decision.receipt_hash}|{decision_time_iso}"
        ),
        "family_id": family.family_id,
        "city": family.city,
        "target_date": family.target_date,
        "temperature_metric": family.metric,
        "decision_id": decision.decision_id,
        "receipt_hash": decision.receipt_hash,
        "state_id": state_id,
        "decision_time": decision_time_iso,
        "causal_snapshot_id": envelope.causal_snapshot_id,
        "predictive_identity_hash": decision.predictive.identity_hash,
        "our_mu_native": decision.predictive.mu_native,
        "our_sigma_native": decision.predictive.sigma_native,
        "measurement_unit": decision.case.resolution.measurement_unit,
        "model_q_json": model_q_json,
        "model_q_identity_hash": model_q_identity_hash,
        "market_center_native": market_center_native(family_book),
        "market_center_status": market_center_status(family_book),
        "market_center_version": MARKET_CENTER_VERSION,
        "complete_book": family_book.complete_book,
        "sampling_reason": sampling_reason,
        "sampling_policy_version": SAMPLING_POLICY_VERSION,
        "capture_seam": "DECISION_PRODUCTION",
        "schema_version": _OBSERVATIONS_SCHEMA_VERSION,
        **market_q_fields(decision),
    }
    if insert_observation(conn, row):
        _cnt_inc(_CNT_WRITTEN_OBSERVATIONS)
    conn.commit()
    _last_state_by_family[family.family_id] = (state_id, envelope.decision_time)
