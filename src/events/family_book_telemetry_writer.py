# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO, plus round-3 review fixes:
#   X1 (transaction poisoning: an explicit transaction per observation, with
#   unconditional rollback -- connection replacement if rollback itself
#   fails -- and counters/sampling-cache updated ONLY after a durable
#   commit); X2 (writer admission: BULK/LIVE are separate lock files with no
#   mutual exclusion by themselves, so this writer no longer touches
#   zeus_trades.db per observation at all -- it writes to a PRIVATE, bounded
#   spool SQLite file with zero contention risk, and a periodic batched
#   ingest pass -- the ONLY code path that ever touches the trade DB --
#   moves durable rows in under db_writer_lock(WriteClass.BULK,
#   blocking=False)); H2 (hot-path purity: the decision thread no longer
#   starts/health-checks the worker or touches the canonical counters lock
#   on its success path).
"""Nonblocking capture plane for family_book_states / family_book_observations.

Two-stage write, both off the live decision thread:

  1. Decision thread: ``enqueue_family_book_observation`` builds a small,
     flat ``ObservationEnvelope`` (src/events/family_book_manifest.py --
     holds NO reference to the FamilyDecision/family/proofs graph, so
     ``FamilyDecision.band.samples`` and similar large objects are not kept
     alive by a queued item) and does a bounded ``queue.put_nowait``. That
     is the ENTIRE hot-path cost -- no worker start/health-check, no locked
     counter increment on the success path.
  2. Writer thread (started explicitly by the daemon at init, never by the
     decision thread -- H2/M1): drains the queue into a PRIVATE spool
     SQLite file (own file, own WAL, zero contention with the primary
     trade_conn -- X2) with explicit per-observation transaction safety
     (X1). A periodic ingest pass -- the only code that ever opens a second
     connection to zeus_trades.db -- copies spool rows into the durable
     trade-DB tables under ``db_writer_lock(WriteClass.BULK,
     blocking=False)``; contention there just defers to the next cycle.

Sampling policy v2 (STATE_CHANGE / HEARTBEAT / PRE_VETO_SELECTED by
precedence, three orthogonal booleans persisted regardless of which one
"won") replaces the broken per-cycle-unique timestamped hash as the
observation-volume control; family_book_states' content_hash dedup controls
STATE row volume.

Kill switch: ``ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED`` (default "1"/on) -- an
operator rollout guard, checked first, before any other work.
"""

from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Sequence

from src.events.family_book_manifest import (
    ObservationEnvelope,
    build_source_manifest,
    compute_state_identity,
    market_center_and_status,
    market_q_json,
    model_q_json,
    project_observation_envelope,
)
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

_ENABLED_ENV_VAR = "ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED"
_QUEUE_MAXSIZE_DEFAULT = 2048
_HEARTBEAT_INTERVAL = timedelta(minutes=30)
_INGEST_INTERVAL_SECONDS = 30.0
_LOG_RATE_LIMIT_SECONDS = 60.0
# SQLite's multi-connection WAL-reset fix landed in 3.51.3 (and backports
# 3.50.7/3.44.6) -- asserted once at worker startup since the ingest pass
# runs a second live writer connection against the trade DB.
_MIN_SQLITE_VERSION_INFO = (3, 51, 3)

# Canonical counter names (src/observability/counters.py sink). Only the
# RARE/exceptional paths route through here from the decision thread (H2);
# the common enqueue-success path touches no lock at all.
_CNT_DROP = "telemetry_drop_total"
_CNT_ENQUEUE_ERROR = "family_book_telemetry_enqueue_error_total"
_CNT_QUEUE_HIGH_WATER = "telemetry_queue_high_water_total"
_CNT_SAMPLED_OUT = "family_book_telemetry_sampled_out_total"
_CNT_WRITE_FAILURES = "family_book_telemetry_write_failures_total"
_CNT_INGEST_CONTENDED = "family_book_telemetry_ingest_contended_total"
_CNT_INGEST_FAILURES = "family_book_telemetry_ingest_failures_total"
_CNT_WRITTEN_STATES = "family_book_telemetry_written_states_total"
_CNT_WRITTEN_OBSERVATIONS = "family_book_telemetry_written_observations_total"
_CNT_INGESTED_STATES = "family_book_telemetry_ingested_states_total"
_CNT_INGESTED_OBSERVATIONS = "family_book_telemetry_ingested_observations_total"

# Sentinel pushed onto the queue to stop the worker (matches
# src/data/replacement_cycle_advance_trigger.py's day0-bridge shape): the
# worker blocks on queue.get() with a bounded timeout (the ingest cadence,
# not a tight poll), so shutdown reacts immediately rather than waiting for
# the next scheduled ingest.
_STOP = object()
_FORCE_INGEST_WAKE = object()


_queue_lock = threading.Lock()
_obs_queue: "queue.Queue[object]" = queue.Queue(maxsize=_QUEUE_MAXSIZE_DEFAULT)
_queue_high_water = 0
_worker_thread: Optional[threading.Thread] = None
_worker_started_lock = threading.Lock()
_last_state_by_family: dict[str, tuple[str, datetime]] = {}
_last_log_monotonic = 0.0
_force_ingest_event = threading.Event()
_force_ingest_done = threading.Event()


def _default_spool_conn_factory() -> sqlite3.Connection:
    """Private telemetry spool -- a SEPARATE physical SQLite file the primary
    trade_conn never touches, so this writer has zero contention risk on its
    frequent per-observation writes (X2)."""
    from src.state.db import _zeus_trade_db_path

    path = _zeus_trade_db_path().with_name("family_book_telemetry_spool.db")
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _default_ingest_conn_factory() -> sqlite3.Connection:
    from src.state.db import get_trade_connection

    return get_trade_connection(busy_timeout_ms=250)


def _default_trade_db_path():
    from src.state.db import _zeus_trade_db_path

    return _zeus_trade_db_path()


_spool_conn_factory: Callable[[], sqlite3.Connection] = _default_spool_conn_factory
_ingest_conn_factory: Callable[[], sqlite3.Connection] = _default_ingest_conn_factory
_trade_db_path_factory: Callable[[], Any] = _default_trade_db_path


def queue_high_water() -> int:
    """Peak queue occupancy observed since the last reset (writer-side; test/ops introspection)."""
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

    The ENTIRE hot-path cost: project a small envelope (attribute reads, no
    JSON/hashing/I/O -- src/events/family_book_manifest.py
    ``project_observation_envelope``), then ``queue.put_nowait``. No worker
    start/health-check (H2 -- the daemon starts the worker at init via
    ``start_worker()``; a dead worker is never resurrected from here), no
    counter-sink lock on the success path.
    """
    if os.environ.get(_ENABLED_ENV_VAR, "1") not in ("1", "true", "True"):
        return
    try:
        envelope = project_observation_envelope(
            decision=decision, family=family, active_proofs=active_proofs,
            candidate_bin_id=candidate_bin_id, decision_time=decision_time,
            causal_snapshot_id=causal_snapshot_id,
        )
        if envelope is None:
            return
        _obs_queue.put_nowait(envelope)
    except queue.Full:
        _cnt_inc(_CNT_DROP)
    except Exception:  # noqa: BLE001 -- must never affect the decision thread
        _cnt_inc(_CNT_ENQUEUE_ERROR)


def start_worker(
    *,
    spool_conn_factory: Optional[Callable[[], sqlite3.Connection]] = None,
    ingest_conn_factory: Optional[Callable[[], sqlite3.Connection]] = None,
    trade_db_path_factory: Optional[Callable[[], Any]] = None,
    maxsize: Optional[int] = None,
) -> bool:
    """Start the writer thread. Called by the daemon at init, never from the
    decision thread (H2). Idempotent: returns False (refuses) while a worker
    is already alive (M1) rather than silently replacing it -- call
    ``shutdown()`` first for a deliberate restart. Tests pass factories
    pointing at isolated file-backed DBs; production leaves all unset."""
    global _spool_conn_factory, _ingest_conn_factory, _trade_db_path_factory
    if _worker_thread is not None and _worker_thread.is_alive():
        return False
    with _worker_started_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return False
        if spool_conn_factory is not None:
            _spool_conn_factory = spool_conn_factory
        if ingest_conn_factory is not None:
            _ingest_conn_factory = ingest_conn_factory
        if trade_db_path_factory is not None:
            _trade_db_path_factory = trade_db_path_factory
        if maxsize is not None:
            _configure_queue(maxsize)
        return _start_worker_locked()


def _configure_queue(maxsize: int) -> None:
    global _obs_queue
    with _queue_lock:
        _obs_queue = queue.Queue(maxsize=maxsize)


def _start_worker_locked() -> bool:
    global _worker_thread
    thread = threading.Thread(
        target=_worker_loop, name="family-book-telemetry-writer", daemon=True
    )
    _worker_thread = thread
    thread.start()
    return True


def shutdown(timeout: float = 5.0) -> bool:
    """Stop the writer thread. Reserves sentinel capacity with a BLOCKING put
    (M1: a full queue must not make shutdown silently no-op) and keeps the
    thread reference until ``join`` actually confirms it dead -- a worker
    that does not die within ``timeout`` is left recorded as still running
    so a caller cannot start a second one on top of it (M1)."""
    global _worker_thread
    thread = _worker_thread
    if thread is None:
        return True
    if not thread.is_alive():
        _worker_thread = None
        return True
    _obs_queue.put(_STOP, timeout=timeout)  # blocking: control-plane, not the decision path
    thread.join(timeout=timeout)
    if thread.is_alive():
        logger.error("family_book_telemetry: worker did not stop within %.1fs", timeout)
        return False
    _worker_thread = None
    return True


def drain(timeout: float = 5.0) -> bool:
    """Block until every item enqueued so far has been WRITTEN TO THE SPOOL
    (not necessarily ingested into the trade DB yet -- see ``force_ingest``).
    Test/ops synchronization helper -- never called from the decision thread."""
    done = threading.Event()
    current_queue = _obs_queue

    def _joiner() -> None:
        current_queue.join()
        done.set()

    threading.Thread(target=_joiner, daemon=True).start()
    return done.wait(timeout=timeout)


def force_ingest(timeout: float = 5.0) -> bool:
    """Test/ops helper: trigger an immediate ingest pass (spool -> trade DB)
    without waiting for the normal 30s cadence, and block until it completes."""
    _force_ingest_done.clear()
    _force_ingest_event.set()
    try:  # wake the worker immediately rather than waiting out its queue.get timeout
        _obs_queue.put_nowait(_FORCE_INGEST_WAKE)
    except queue.Full:
        pass
    return _force_ingest_done.wait(timeout=timeout)


def reset_for_test() -> None:
    """Full reset: stop worker, clear queue/sampling cache/high-water, restore
    default factories, reset the canonical counters sink. Test-only
    (``reset_all()`` is documented in src/observability/counters.py as
    test-support-only)."""
    global _spool_conn_factory, _ingest_conn_factory, _trade_db_path_factory, _queue_high_water
    shutdown()
    _spool_conn_factory = _default_spool_conn_factory
    _ingest_conn_factory = _default_ingest_conn_factory
    _trade_db_path_factory = _default_trade_db_path
    _configure_queue(_QUEUE_MAXSIZE_DEFAULT)
    _last_state_by_family.clear()
    _queue_high_water = 0
    _force_ingest_event.clear()
    _force_ingest_done.clear()
    from src.observability.counters import reset_all

    reset_all()


def counter(name: str) -> int:
    """Read back a named counter via the canonical sink (test/ops helper)."""
    return _cnt_read(name)


def _rate_limited_warning(msg: str, *args: Any) -> None:
    global _last_log_monotonic
    now = time.monotonic()
    if now - _last_log_monotonic >= _LOG_RATE_LIMIT_SECONDS:
        _last_log_monotonic = now
        logger.warning(msg, *args, exc_info=True)


# ---------------------------------------------------------------------------
# Worker loop: spool writes (frequent, uncontended) + periodic ingest
# (infrequent, the only trade-DB touch).
# ---------------------------------------------------------------------------

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
        conn = _spool_conn_factory()
        _ensure_states_table(conn)
        _ensure_observations_table(conn)
    except Exception:
        logger.warning("family_book_telemetry: spool init failed", exc_info=True)
        return

    _bootstrap_last_state_cache()

    last_ingest_at = time.monotonic()
    try:
        while True:
            try:
                item = _obs_queue.get(timeout=_INGEST_INTERVAL_SECONDS)
            except queue.Empty:
                pass
            else:
                try:
                    if item is _STOP:
                        return
                    if item is not _FORCE_INGEST_WAKE:
                        conn = _process_one(conn, item)
                        global _queue_high_water
                        size = _obs_queue.qsize()
                        if size > _queue_high_water:
                            _queue_high_water = size
                            _cnt_inc(_CNT_QUEUE_HIGH_WATER)
                finally:
                    _obs_queue.task_done()

            now = time.monotonic()
            due = now - last_ingest_at >= _INGEST_INTERVAL_SECONDS
            forced = _force_ingest_event.is_set()
            if due or forced:
                _force_ingest_event.clear()
                _ingest_pass(conn)
                last_ingest_at = now
                if forced:
                    _force_ingest_done.set()
    finally:
        conn.close()


def _bootstrap_last_state_cache() -> None:
    """M2: seed the sampling cache from the durable trade DB's latest
    observation per family, so a worker restart does not falsely relabel an
    unchanged state STATE_CHANGE / reset the heartbeat clock. Best-effort --
    any failure just leaves the cache empty (matches pre-fix behavior, never
    blocks worker startup)."""
    try:
        conn = _ingest_conn_factory()
    except Exception:
        return
    try:
        _ensure_states_table(conn)
        _ensure_observations_table(conn)
        rows = conn.execute(
            """
            SELECT family_id, state_id, MAX(decision_time) AS decision_time
            FROM family_book_observations
            GROUP BY family_id
            """
        ).fetchall()
        for family_id, state_id, decision_time_iso in rows:
            try:
                _last_state_by_family[family_id] = (
                    state_id, datetime.fromisoformat(decision_time_iso)
                )
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    finally:
        conn.close()


def _process_one(conn: sqlite3.Connection, envelope: ObservationEnvelope) -> sqlite3.Connection:
    """Write one envelope to the spool with explicit transaction safety (X1).

    Returns the connection to use going forward -- unchanged on success or an
    ordinary failure (rolled back cleanly), REPLACED if rollback itself
    failed (the only way to guarantee ``conn.in_transaction`` is False).
    """
    state_id, content_hash, canonical_payload = compute_state_identity(envelope)
    decision_time_iso = envelope.decision_time.isoformat()

    state_changed, heartbeat_due, sampling_reason = _sampling_decision(
        envelope.family_id, state_id, envelope.decision_time, envelope.pre_veto_selected
    )
    if sampling_reason is None:
        _cnt_inc(_CNT_SAMPLED_OUT)
        return conn

    center_value, center_status = market_center_and_status(envelope)
    row = {
        "observation_id": _sha256_text(f"{envelope.family_id}|{envelope.receipt_hash}|{decision_time_iso}"),
        "family_id": envelope.family_id,
        "city": envelope.city,
        "target_date": envelope.target_date,
        "temperature_metric": envelope.temperature_metric,
        "decision_id": envelope.decision_id,
        "receipt_hash": envelope.receipt_hash,
        "state_id": state_id,
        "source_manifest_json": build_source_manifest(envelope),
        "decision_time": decision_time_iso,
        "causal_snapshot_id": envelope.causal_snapshot_id,
        "predictive_identity_hash": envelope.predictive_identity_hash,
        "our_mu_native": envelope.our_mu_native,
        "our_sigma_native": envelope.our_sigma_native,
        "measurement_unit": envelope.measurement_unit,
        "model_q_json": model_q_json(envelope),
        "model_q_identity_hash": envelope.model_q_identity_hash,
        "market_q_json": market_q_json(envelope),
        "market_q_basis": envelope.market_q_basis,
        "market_q_depth_score": envelope.market_q_depth_score,
        "market_q_spread_score": envelope.market_q_spread_score,
        "market_q_projection_error": envelope.market_q_projection_error,
        "market_q_book_hash": envelope.market_q_book_hash,
        "market_center_native": center_value,
        "market_center_status": center_status,
        "market_center_version": MARKET_CENTER_VERSION,
        "complete_book": envelope.complete_book,
        "sampling_reason": sampling_reason,
        "state_changed": state_changed,
        "heartbeat_due": heartbeat_due,
        "pre_veto_selected": envelope.pre_veto_selected,
        "selected_bin_id": envelope.selected_bin_id,
        "selected_side": envelope.selected_side,
        "sampling_policy_version": SAMPLING_POLICY_VERSION,
        "capture_seam": "DECISION_PRODUCTION",
        "schema_version": _OBSERVATIONS_SCHEMA_VERSION,
    }

    try:
        inserted_state = insert_state(
            conn, state_id=state_id, family_id=envelope.family_id, content_hash=content_hash,
            topology_hash=envelope.topology_hash, complete_book=envelope.complete_book,
            canonical_payload=canonical_payload, first_seen_decision_time=decision_time_iso,
        )
        inserted_obs = insert_observation(conn, row)
        conn.commit()
    except BaseException:
        conn = _rollback_or_replace(conn, _spool_conn_factory)
        _cnt_inc(_CNT_WRITE_FAILURES)
        _rate_limited_warning("family_book_telemetry: spool write failed")
        return conn

    # Counters/cache updated ONLY after a durable commit (X1).
    if inserted_state:
        _cnt_inc(_CNT_WRITTEN_STATES)
    if inserted_obs:
        _cnt_inc(_CNT_WRITTEN_OBSERVATIONS)
    _last_state_by_family[envelope.family_id] = (state_id, envelope.decision_time)
    return conn


def _rollback_or_replace(
    conn: sqlite3.Connection, conn_factory: Callable[[], sqlite3.Connection]
) -> sqlite3.Connection:
    """Guarantee the returned connection is NOT mid-transaction (X1): try
    rollback first; if rollback itself fails, close and open a fresh
    connection instead (the only way SQLite can guarantee a clean slate)."""
    try:
        conn.rollback()
        return conn
    except Exception:
        _rate_limited_warning("family_book_telemetry: rollback failed, replacing connection")
        try:
            conn.close()
        except Exception:
            pass
        new_conn = conn_factory()
        _ensure_states_table(new_conn)
        _ensure_observations_table(new_conn)
        return new_conn


def _sampling_decision(
    family_id: str, state_id: str, decision_time: datetime, pre_veto_selected: bool
) -> tuple[bool, bool, Optional[str]]:
    """Return (state_changed, heartbeat_due, sampling_reason). The three
    conditions are orthogonal and persisted regardless of which "wins" by
    precedence (STATE_CHANGE > HEARTBEAT > PRE_VETO_SELECTED); None means
    sampled out (H3: selection-triggered sampling is not missing-at-random,
    so downstream analysts must be able to see ALL of what held, not just
    the winning reason)."""
    last = _last_state_by_family.get(family_id)
    state_changed = last is None or state_id != last[0]
    heartbeat_due = (
        last is not None and not state_changed
        and decision_time - last[1] >= _HEARTBEAT_INTERVAL
    )
    if state_changed:
        reason = "STATE_CHANGE"
    elif heartbeat_due:
        reason = "HEARTBEAT"
    elif pre_veto_selected:
        reason = "PRE_VETO_SELECTED"
    else:
        reason = None
    return state_changed, heartbeat_due, reason


def _sha256_text(value: str) -> str:
    from src.events.idempotency import sha256_text

    return sha256_text(value)


# ---------------------------------------------------------------------------
# Ingest pass: spool -> durable trade DB. The ONLY code here that ever opens
# a connection to zeus_trades.db (X2) -- infrequent (every
# ``_INGEST_INTERVAL_SECONDS``) and batched, under db_writer_lock.
# ---------------------------------------------------------------------------

def _ingest_pass(spool_conn: sqlite3.Connection) -> None:
    trade_db_path = _trade_db_path_factory()
    try:
        ingest_conn = _ingest_conn_factory()
    except Exception:
        _rate_limited_warning("family_book_telemetry: ingest connection failed")
        return
    try:
        _ensure_states_table(ingest_conn)
        _ensure_observations_table(ingest_conn)
        try:
            with db_writer_lock(trade_db_path, WriteClass.BULK, blocking=False):
                _ingest_states(spool_conn, ingest_conn)
                _ingest_observations(spool_conn, ingest_conn)
        except BlockingIOError:
            _cnt_inc(_CNT_INGEST_CONTENDED)
    finally:
        ingest_conn.close()


def _ingest_states(spool_conn: sqlite3.Connection, ingest_conn: sqlite3.Connection) -> None:
    spool_conn.row_factory = sqlite3.Row
    rows = spool_conn.execute(
        "SELECT state_id, family_id, content_hash, hash_version, topology_hash, "
        "complete_book, canonical_payload, payload_schema_version, first_seen_decision_time "
        "FROM family_book_states"
    ).fetchall()
    try:
        for r in rows:
            if insert_state(
                ingest_conn, state_id=r["state_id"], family_id=r["family_id"],
                content_hash=r["content_hash"], hash_version=r["hash_version"],
                topology_hash=r["topology_hash"], complete_book=bool(r["complete_book"]),
                canonical_payload=r["canonical_payload"],
                payload_schema_version=r["payload_schema_version"],
                first_seen_decision_time=r["first_seen_decision_time"],
            ):
                _cnt_inc(_CNT_INGESTED_STATES)
        ingest_conn.commit()
    except BaseException:
        _safe_rollback(ingest_conn)
        _cnt_inc(_CNT_INGEST_FAILURES)
        _rate_limited_warning("family_book_telemetry: state ingest failed")
        raise


def _ingest_observations(spool_conn: sqlite3.Connection, ingest_conn: sqlite3.Connection) -> None:
    spool_conn.row_factory = sqlite3.Row
    from src.state.schema.family_book_observations_schema import _COLUMNS

    rows = spool_conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM family_book_observations"
    ).fetchall()
    try:
        for r in rows:
            if insert_observation(ingest_conn, dict(r)):
                _cnt_inc(_CNT_INGESTED_OBSERVATIONS)
        ingest_conn.commit()
    except BaseException:
        _safe_rollback(ingest_conn)
        _cnt_inc(_CNT_INGEST_FAILURES)
        _rate_limited_warning("family_book_telemetry: observation ingest failed")
        raise


def _safe_rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except Exception:
        pass
