# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO (BLOCKER 1: synchronous JSON+SQLite on the
#   live decision thread cannot bound latency -- an exception handler cannot
#   undo elapsed WAL-writer-lock wait time; the repository's default busy
#   timeout is 30s and SQLite WAL permits only one writer at a time).
"""Nonblocking capture plane for family_book_states / family_book_observations.

The decision thread calls ONLY ``enqueue_family_book_observation`` -- a bounded
``queue.put_nowait`` of a small envelope of already-in-memory object
references. Zero serialization, zero SQLite, zero blocking; a full queue
increments a drop counter and returns immediately. All manifest-building,
hashing, JSON serialization, sampling-policy evaluation, and SQLite I/O happen
on a separate owner-local writer thread that opens its OWN trade-DB connection
with a short busy_timeout (``get_trade_connection(busy_timeout_ms=...)`` --
the repo's sanctioned "optional derived publication yields to live writers"
carve-out; see src/state/db.py ``_connect`` docstring. Connection PRAGMA only,
INV-37 / transaction semantics unchanged -- no ATTACH, no shared transaction
with the live trade_conn).

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


@dataclass(frozen=True)
class _ObservationEnvelope:
    """Immutable references only -- no copying, no serialization at enqueue time."""

    decision: Any  # FamilyDecision
    family: Any  # EventBoundCandidateFamily
    active_proofs: tuple
    candidate_bin_id: Callable[[Any], str]
    decision_time: datetime
    causal_snapshot_id: Optional[str]


class _Counters:
    """Thread-safe named counters (drop/write/sample telemetry, never the DB)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, int] = {}

    def increment(self, name: str, by: int = 1) -> None:
        with self._lock:
            self._values[name] = self._values.get(name, 0) + by

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


COUNTERS = _Counters()

_queue_lock = threading.Lock()
_obs_queue: "queue.Queue[_ObservationEnvelope]" = queue.Queue(maxsize=_QUEUE_MAXSIZE_DEFAULT)
_worker_thread: Optional[threading.Thread] = None
_worker_started_lock = threading.Lock()
_shutdown_event = threading.Event()
_last_state_by_family: dict[str, tuple[str, datetime]] = {}
_last_log_monotonic = 0.0


def _default_conn_factory() -> sqlite3.Connection:
    from src.state.db import get_trade_connection

    return get_trade_connection(busy_timeout_ms=_WRITER_BUSY_TIMEOUT_MS)


_conn_factory: Callable[[], sqlite3.Connection] = _default_conn_factory


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
        COUNTERS.increment("enqueued")
    except queue.Full:
        COUNTERS.increment("dropped_queue_full")
    except Exception:  # noqa: BLE001 -- must never affect the decision thread
        COUNTERS.increment("enqueue_error")


def start_worker(
    *,
    conn_factory: Optional[Callable[[], sqlite3.Connection]] = None,
    maxsize: Optional[int] = None,
) -> None:
    """Explicitly (re)start the writer thread. Idempotent if already running
    with no override. Tests pass ``conn_factory`` pointing at an isolated
    file-backed DB; production leaves both args unset (real trade DB)."""
    global _conn_factory
    if conn_factory is not None:
        _conn_factory = conn_factory
    if maxsize is not None:
        _configure_queue(maxsize)
    _ensure_worker_started(force=True)


def _configure_queue(maxsize: int) -> None:
    global _obs_queue
    with _queue_lock:
        _obs_queue = queue.Queue(maxsize=maxsize)


def _ensure_worker_started(*, force: bool = False) -> None:
    global _worker_thread
    if not force and _worker_thread is not None and _worker_thread.is_alive():
        return
    with _worker_started_lock:
        if not force and _worker_thread is not None and _worker_thread.is_alive():
            return
        if force and _worker_thread is not None and _worker_thread.is_alive():
            _shutdown_event.set()
            _worker_thread.join(timeout=5.0)
        _shutdown_event.clear()
        thread = threading.Thread(
            target=_worker_loop, name="family-book-telemetry-writer", daemon=True
        )
        _worker_thread = thread
        thread.start()


def shutdown(timeout: float = 5.0) -> None:
    """Stop the writer thread (test/ops lifecycle helper)."""
    global _worker_thread
    _shutdown_event.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout=timeout)
        _worker_thread = None


def drain(timeout: float = 5.0) -> bool:
    """Block until every item enqueued so far has been processed, or timeout.

    Test/ops synchronization helper -- never called from the decision thread.
    Returns True iff the queue fully drained within ``timeout``.
    """
    done = threading.Event()

    def _joiner() -> None:
        _obs_queue.join()
        done.set()

    threading.Thread(target=_joiner, daemon=True).start()
    return done.wait(timeout=timeout)


def reset_for_test() -> None:
    """Full reset: stop worker, clear queue/counters/sampling cache, restore
    the default connection factory. Test-only."""
    shutdown()
    global _conn_factory
    _conn_factory = _default_conn_factory
    _configure_queue(_QUEUE_MAXSIZE_DEFAULT)
    COUNTERS.reset()
    _last_state_by_family.clear()
    _shutdown_event.clear()


def _worker_loop() -> None:
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
    try:
        while not _shutdown_event.is_set():
            try:
                envelope = _obs_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            _process_one(conn, envelope)
            _obs_queue.task_done()
    finally:
        conn.close()


def _rate_limited_warning(msg: str) -> None:
    global _last_log_monotonic
    now = time.monotonic()
    if now - _last_log_monotonic >= _LOG_RATE_LIMIT_SECONDS:
        _last_log_monotonic = now
        logger.warning(msg, exc_info=True)


def _process_one(conn: sqlite3.Connection, envelope: _ObservationEnvelope) -> None:
    try:
        _write_observation(conn, envelope)
    except Exception:
        COUNTERS.increment("write_failures")
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
        COUNTERS.increment("sampled_out")
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
        COUNTERS.increment("written_states")

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
        COUNTERS.increment("written_observations")
    conn.commit()
    _last_state_by_family[family.family_id] = (state_id, envelope.decision_time)
