# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after two deep-review NO-GOs. Round-4 found the round-3 "spool"
#   was a full unbounded mirror of the canonical append-only schemas
#   (fetchall() of ALL history every 30s, no watermark, no-delete triggers --
#   it could never drain) and that the worker's own canonical ingest pass
#   was a second, uncoordinated writer against the live-money DB that could
#   also crash the sole worker on ordinary SQLite contention. Round-5
#   (this revision) fixes both: X1's transaction-safety STRUCTURE
#   (_rollback_or_replace) is UNCHANGED per explicit reviewer confirmation --
#   only what it wraps changed (a bounded outbox row, not the canonical
#   tables directly).
"""Nonblocking capture plane, cleanly split from canonical delivery.

**Capture (this module's worker thread)**: the decision thread calls ONLY
``enqueue_family_book_observation`` -- project a compact envelope
(src/events/family_book_manifest.py, no reference to FamilyDecision/family/
proofs), then ``queue.put_nowait``. The worker thread drains that queue into
a PRIVATE spool file (``family_book_telemetry_spool.db``) -- own file, own
WAL, zero contention with the trade DB by construction, since nothing else
ever opens it. Writes land in a bounded, DELETABLE outbox table
(``family_book_telemetry_outbox_schema.py`` -- the fix for round-4's
"unbounded mirror" finding). The worker thread NEVER opens the canonical
trade DB for writing (round-4's "second uncoordinated writer" finding); its
only canonical touch is a one-shot READ-ONLY cache seed at startup.

**Delivery (``run_bounded_ingest``, called by the DAEMON, not this module's
worker thread)**: a small, pure function that reads ONE bounded batch from
the outbox (row/byte budget), inserts it into the canonical
family_book_states/family_book_observations tables in one transaction
(targeted upserts, idempotent), commits, and ONLY THEN deletes that batch
from the outbox in a separate transaction. The daemon runs this on its own
``get_trade_connection(write_class="live")`` connection via an ordinary
APScheduler job (src/main.py) -- the SAME pattern every other periodic
trade-DB touch in this daemon already uses, not a novel standalone-writer
class requiring new arbitration machinery.
"""

from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from src.state.schema.family_book_telemetry_outbox_schema import (
    delete_up_to as _outbox_delete_up_to,
    ensure_table as _ensure_outbox_table,
    fetch_batch as _outbox_fetch_batch,
    insert_outbox_row,
    latest_per_family as _outbox_latest_per_family,
    pending_budget as _outbox_pending_budget,
    pending_stats as _outbox_pending_stats,
)

logger = logging.getLogger(__name__)

_ENABLED_ENV_VAR = "ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED"
_QUEUE_MAXSIZE_DEFAULT = 2048
_HEARTBEAT_INTERVAL = timedelta(minutes=30)
_LOG_RATE_LIMIT_SECONDS = 60.0
_WORKER_POLL_SECONDS = 0.1  # bounded shutdown latency; no periodic work left in the loop
_READY_TIMEOUT_DEFAULT = 5.0
# Round-6 Z2: two DISTINCT ceilings, named for what each actually is. The
# first is an ADMISSION threshold on undelivered payload bytes -- a soft gate
# the capture path consults and honors. The second is a PHYSICAL ceiling
# SQLite enforces on the spool file itself: past it the engine returns
# SQLITE_FULL to this module's own writes, so a bug in the soft gate (or a
# burst that outruns it) still cannot eat the disk the live-money DB needs.
# The physical ceiling is denominated in BYTES and converted to
# PRAGMA max_page_count using the connection's ACTUAL page_size at open time
# (src.state.db.page_count_ceiling_for_byte_budget) -- never an assumed
# 4 KiB, which would silently change the real ceiling if the spool file's
# page size ever differs from that assumption. 1 GiB, deliberately well above
# the 500 MB admission threshold so the soft gate is what normally binds and
# the hard ceiling is a backstop, not a routine limit.
_SPOOL_PENDING_BUDGET_BYTES_DEFAULT = 500_000_000  # admission threshold
_SPOOL_MAX_BYTES_DEFAULT = 1_073_741_824  # physical file ceiling (~1 GiB, real page_size)
_INGEST_BATCH_SIZE_DEFAULT = 500
_INGEST_BYTE_BUDGET_DEFAULT = 5_000_000  # 5 MB canonical transaction cap
# SQLite's multi-connection WAL-reset fix landed in 3.51.3 (and backports
# 3.50.7/3.44.6). The daemon ALSO gates this at boot
# (src.state.db.assert_sqlite_version_safe) -- this is a scoped, defense-in-
# depth check specific to this module's own second-connection contract.
_MIN_SQLITE_VERSION_INFO = (3, 51, 3)

# Canonical counter names (src/observability/counters.py sink).
_CNT_DROP = "telemetry_drop_total"
_CNT_ENQUEUE_ERROR = "family_book_telemetry_enqueue_error_total"
_CNT_QUEUE_HIGH_WATER = "telemetry_queue_high_water_total"
_CNT_SAMPLED_OUT = "family_book_telemetry_sampled_out_total"
_CNT_WRITE_FAILURES = "family_book_telemetry_write_failures_total"
_CNT_MALFORMED_ENVELOPE = "family_book_telemetry_malformed_envelope_total"
_CNT_SPOOL_BUDGET_EXCEEDED = "family_book_telemetry_spool_budget_exceeded_total"
_CNT_SPOOL_BUDGET_UNREADABLE = "family_book_telemetry_spool_budget_unreadable_total"
_CNT_ACK_FAILURES = "family_book_telemetry_ack_failures_total"
_CNT_WORKER_ITEM_ERRORS = "family_book_telemetry_worker_item_error_total"
_CNT_INGEST_DEFERRED = "family_book_telemetry_ingest_deferred_total"
_CNT_WRITTEN_OUTBOX = "family_book_telemetry_written_outbox_total"
_CNT_STARTUP_FAILED = "family_book_telemetry_startup_failed_total"
_CNT_INGEST_FAILURES = "family_book_telemetry_ingest_failures_total"
_CNT_INGEST_DISABLED = "family_book_telemetry_ingest_disabled_total"
_CNT_INGESTED_STATES = "family_book_telemetry_ingested_states_total"
_CNT_INGESTED_OBSERVATIONS = "family_book_telemetry_ingested_observations_total"
_CNT_INGESTED_BATCHES = "family_book_telemetry_ingested_batches_total"
# Distinct from the generic _CNT_INGEST_FAILURES: this fires specifically
# when the evidence DB's OWN physical growth ceiling
# (src.state.db.init_schema_family_book_evidence's max_page_count) is what
# refused the write -- a logged, counted, TYPED refusal (never a silent
# drop) so the operator can tell "our own ceiling bound growth as designed"
# apart from an unrelated real disk-full condition, both of which SQLite
# reports as the same "database or disk is full" error text.
_CNT_INGEST_EVIDENCE_DB_CEILING_HIT = "family_book_telemetry_evidence_db_ceiling_hit_total"


def _telemetry_enabled() -> bool:
    return os.environ.get(_ENABLED_ENV_VAR, "1") in ("1", "true", "True")


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    reason: Optional[str] = None
    # False iff the worker started but could not seed its last-state cache:
    # capture is correct, but the first observation per family is labelled
    # STATE_CHANGE regardless of content. Distinct from ``ready`` so the
    # caveat is visible instead of silently folded into success.
    cache_seeded: bool = True


@dataclass(frozen=True)
class IngestOutcome:
    """Result of ONE bounded run_bounded_ingest() call."""

    batch_rows: int
    ingested_states: int
    ingested_observations: int
    disabled: bool = False
    contended: bool = False
    failed: bool = False
    # Canonical commit succeeded but the spool ack (delete) did not: data is
    # durable, the outbox will replay this batch. Distinct from ``failed``,
    # which means nothing was delivered.
    ack_failed: bool = False
    deferred: bool = False
    reason: Optional[str] = None


_queue_lock = threading.Lock()
_obs_queue: "queue.Queue[ObservationEnvelope]" = queue.Queue(maxsize=_QUEUE_MAXSIZE_DEFAULT)
_queue_high_water = 0
_worker_thread: Optional[threading.Thread] = None
_worker_started_lock = threading.Lock()
_stop_event = threading.Event()
_ready_event = threading.Event()
_last_readiness: Optional[ReadinessResult] = None
_capture_terminally_disabled = False
_last_state_by_family: dict[str, tuple[str, datetime]] = {}
_last_log_monotonic = 0.0
_spool_pending_budget_bytes = _SPOOL_PENDING_BUDGET_BYTES_DEFAULT
_spool_max_bytes = _SPOOL_MAX_BYTES_DEFAULT


def _assert_path_is_not_canonical(path: Path) -> None:
    """LOW (centralized): the spool must never alias ANY canonical DB file --
    world, forecasts, trade, or the family-book evidence DB -- not just the
    trade DB. ``resolve()`` equality catches symbolic-construction bugs (a
    typo'd ``.with_name`` producing a canonical name); it does NOT catch
    filesystem aliasing -- a symlink or hardlink under a different name
    pointing at the same inode. ``os.path.samefile`` compares device+inode
    and catches that too, for every canonical path that actually exists on
    disk (a not-yet-created canonical DB cannot be aliased)."""
    import os as _os

    from src.state.db import (
        ZEUS_FAMILY_BOOK_EVIDENCE_DB_PATH,
        ZEUS_FORECASTS_DB_PATH,
        ZEUS_WORLD_DB_PATH,
        _zeus_trade_db_path,
    )

    canonical_paths = (
        ZEUS_WORLD_DB_PATH,
        ZEUS_FORECASTS_DB_PATH,
        ZEUS_FAMILY_BOOK_EVIDENCE_DB_PATH,
        _zeus_trade_db_path(),
    )
    for canonical in canonical_paths:
        assert path.resolve() != canonical.resolve(), (
            f"family_book_telemetry spool path must never equal a canonical DB path: {canonical}"
        )
        if path.exists() and canonical.exists() and _os.path.samefile(path, canonical):
            raise AssertionError(
                f"family_book_telemetry spool path aliases canonical DB path "
                f"{canonical} via a filesystem link (symlink/hardlink)"
            )


def _default_spool_conn_factory() -> sqlite3.Connection:
    """The ONLY ``sqlite3.connect()`` call in this module (enforced by
    ``tests/events/test_family_book_telemetry_writer.py``'s AST antibody --
    round-4 MEDIUM: the SQLITE_CONNECT_ALLOWLIST exemption is file-scoped,
    so this test compensates at function granularity). Opens a PRIVATE
    file no canonical connection NEVER touches -- asserted, not just
    documented (see _assert_path_is_not_canonical)."""
    from src.state.db import _zeus_trade_db_path

    spool_path = _zeus_trade_db_path().with_name("family_book_telemetry_spool.db")
    _assert_path_is_not_canonical(spool_path)
    conn = sqlite3.connect(str(spool_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _open_spool(factory: Callable[[], sqlite3.Connection]) -> sqlite3.Connection:
    """Open a spool connection with its schema and PHYSICAL ceilings armed.
    Every spool open in this module goes through here, so no path can acquire
    an unbounded spool file (round-6 Z2). The byte budget is converted to
    ``max_page_count`` using THIS connection's actual ``page_size`` -- never
    an assumed 4 KiB -- via ``src.state.db.page_count_ceiling_for_byte_budget``."""
    conn = factory()
    try:
        from src.state.db import page_count_ceiling_for_byte_budget

        max_pages = page_count_ceiling_for_byte_budget(conn, _spool_max_bytes)
        _ensure_outbox_table(conn, max_page_count=max_pages)
    except BaseException:
        conn.close()
        raise
    return conn


def _default_readonly_canonical_conn_factory() -> sqlite3.Connection:
    """Best-effort READ-ONLY canonical connection for cache bootstrap only
    (Y2: no DDL, no write_class, mode=ro+query_only -- never a writer).

    DB split (2026-08-19): family_book_states/family_book_observations moved
    off zeus_trades.db onto their own file, state/zeus-family-book-evidence.db
    -- this seed read (and canonical delivery, run_bounded_ingest's caller in
    src/main.py) now targets THAT DB, never the trade DB."""
    from src.state.db import get_family_book_evidence_connection_read_only

    return get_family_book_evidence_connection_read_only()


_spool_conn_factory: Callable[[], sqlite3.Connection] = _default_spool_conn_factory
_readonly_canonical_conn_factory: Callable[[], sqlite3.Connection] = _default_readonly_canonical_conn_factory


def queue_high_water() -> int:
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
    """Bounded, nonblocking enqueue -- call ONLY from the live decision thread."""
    if _capture_terminally_disabled or not _telemetry_enabled():
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
    readonly_canonical_conn_factory: Optional[Callable[[], sqlite3.Connection]] = None,
    maxsize: Optional[int] = None,
    spool_pending_budget_bytes: Optional[int] = None,
    ready_timeout: float = _READY_TIMEOUT_DEFAULT,
) -> ReadinessResult:
    """Start the capture-side worker thread and BLOCK for a ready/failed
    handshake (Y5). Called by the daemon at init, before reactor activation,
    never from the decision thread. Idempotent: returns the LAST readiness
    result unchanged while a worker is already alive rather than starting a
    second one. On failure, capture is disabled TERMINALLY (a typed counter
    fires; no retry loop from the decision thread -- see
    ``enqueue_family_book_observation``)."""
    global _spool_conn_factory, _readonly_canonical_conn_factory, _spool_pending_budget_bytes
    if _worker_thread is not None and _worker_thread.is_alive():
        return _last_readiness or ReadinessResult(ready=True)
    with _worker_started_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return _last_readiness or ReadinessResult(ready=True)
        if spool_conn_factory is not None:
            _spool_conn_factory = spool_conn_factory
        if readonly_canonical_conn_factory is not None:
            _readonly_canonical_conn_factory = readonly_canonical_conn_factory
        if spool_pending_budget_bytes is not None:
            _spool_pending_budget_bytes = spool_pending_budget_bytes
        if maxsize is not None:
            _configure_queue(maxsize)
        return _start_worker_locked(ready_timeout)


def _configure_queue(maxsize: int) -> None:
    global _obs_queue
    with _queue_lock:
        _obs_queue = queue.Queue(maxsize=maxsize)


def _start_worker_locked(ready_timeout: float) -> ReadinessResult:
    global _worker_thread, _capture_terminally_disabled
    _stop_event.clear()
    _ready_event.clear()
    thread = threading.Thread(
        target=_worker_loop, name="family-book-telemetry-writer", daemon=True
    )
    _worker_thread = thread
    thread.start()
    if not _ready_event.wait(timeout=ready_timeout):
        # Round-6: a timeout means the thread is SLOW, not absent -- it may
        # still be inside _open_spool and will happily come up and start
        # writing after we have declared capture dead. Stop it and JOIN before
        # returning, so "not ready" means no live worker, not an unsupervised
        # one. Cleanup is keyed on "was started", never on "became ready".
        _stop_event.set()
        thread.join(timeout=ready_timeout)
        if thread.is_alive():
            logger.error("family_book_telemetry: worker survived startup-timeout join")
        _worker_thread = None
        result = ReadinessResult(ready=False, reason="startup_timeout")
    else:
        result = _last_readiness or ReadinessResult(ready=False, reason="unknown")
    if not result.ready:
        _capture_terminally_disabled = True
        _cnt_inc(_CNT_STARTUP_FAILED)
        logger.error("family_book_telemetry: worker failed to start: %s", result.reason)
    return result


def shutdown(timeout: float = 5.0) -> bool:
    """Stop the writer thread via a stop Event (round-4 MEDIUM: a blocking
    sentinel ``put()`` can itself raise ``queue.Full`` if the queue stays
    full while the worker is wedged; a stop event has no such failure mode).
    Keeps the thread reference until ``join`` actually confirms it dead."""
    global _worker_thread
    thread = _worker_thread
    if thread is None:
        return True
    if not thread.is_alive():
        _worker_thread = None
        return True
    _stop_event.set()
    thread.join(timeout=timeout)
    if thread.is_alive():
        logger.error("family_book_telemetry: worker did not stop within %.1fs", timeout)
        return False
    _worker_thread = None
    return True


def drain(timeout: float = 5.0) -> bool:
    """Block until every item enqueued so far has been written to the spool.
    Test/ops synchronization helper -- never called from the decision thread."""
    done = threading.Event()
    current_queue = _obs_queue

    def _joiner() -> None:
        current_queue.join()
        done.set()

    threading.Thread(target=_joiner, daemon=True).start()
    return done.wait(timeout=timeout)


def reset_for_test() -> None:
    global _spool_conn_factory, _readonly_canonical_conn_factory, _queue_high_water
    global _capture_terminally_disabled, _spool_pending_budget_bytes, _last_readiness
    shutdown()
    _spool_conn_factory = _default_spool_conn_factory
    _readonly_canonical_conn_factory = _default_readonly_canonical_conn_factory
    _configure_queue(_QUEUE_MAXSIZE_DEFAULT)
    _last_state_by_family.clear()
    _queue_high_water = 0
    _capture_terminally_disabled = False
    _spool_pending_budget_bytes = _SPOOL_PENDING_BUDGET_BYTES_DEFAULT
    _last_readiness = None
    _stop_event.clear()
    _ready_event.clear()
    from src.observability.counters import reset_all

    reset_all()


def counter(name: str) -> int:
    return _cnt_read(name)


def _rate_limited_warning(msg: str, *args: Any) -> None:
    global _last_log_monotonic
    now = time.monotonic()
    if now - _last_log_monotonic >= _LOG_RATE_LIMIT_SECONDS:
        _last_log_monotonic = now
        logger.warning(msg, *args, exc_info=True)


# ---------------------------------------------------------------------------
# Worker loop: capture ONLY -- spool writes, and no WRITABLE canonical access
# ever. (It does open canonical READ-ONLY exactly once, at startup, to seed the
# last-state cache -- see _bootstrap_last_state_cache. "No canonical access at
# all" would be a comment that lies about its own module.)
# ---------------------------------------------------------------------------

def _worker_loop() -> None:
    global _last_readiness
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION_INFO:
        _last_readiness = ReadinessResult(
            ready=False,
            reason=f"sqlite {sqlite3.sqlite_version} below WAL-reset-fix floor {_MIN_SQLITE_VERSION_INFO}",
        )
        _ready_event.set()
        return
    try:
        conn = _open_spool(_spool_conn_factory)
    except Exception as exc:
        _last_readiness = ReadinessResult(ready=False, reason=f"spool init failed: {exc!r}")
        _ready_event.set()
        logger.warning("family_book_telemetry: spool init failed", exc_info=True)
        return

    # Round-6: seeding is a SEPARATE fact from readiness. A worker that came up
    # with an unseeded cache is ready (it captures correctly) but will label the
    # first observation of each family STATE_CHANGE even if the content is
    # unchanged -- a real, bounded analysis caveat the operator can only account
    # for if it is reported rather than folded into a single ready=True.
    #
    # HIGH-2 fix: cache_seeded must reflect whether the canonical READ actually
    # completed, not merely whether _bootstrap_last_state_cache raised.
    # _bootstrap_last_state_cache used to swallow its own canonical-read
    # failure internally (a bare `except Exception: return`) and return
    # normally either way -- so this try/except never fired and cache_seeded
    # stayed True even when the canonical seed read never happened.
    # _bootstrap_last_state_cache now raises CanonicalSeedUnavailableError
    # for every outcome EXCEPT a positively-identified read (rows fetched) or
    # a positively-identified fresh-empty-schema (no such table -- there is
    # nothing to seed, so that case IS fully seeded).
    cache_seeded = True
    try:
        _bootstrap_last_state_cache(conn)
    except CanonicalSeedUnavailableError as exc:
        cache_seeded = False
        logger.warning("family_book_telemetry: cache bootstrap seed unavailable: %s", exc)

    _last_readiness = ReadinessResult(ready=True, cache_seeded=cache_seeded)
    _ready_event.set()

    try:
        while not _stop_event.is_set():
            try:
                envelope = _obs_queue.get(timeout=_WORKER_POLL_SECONDS)
            except queue.Empty:
                continue
            try:
                # Round-6: a supervisor boundary around the WHOLE per-item body.
                # _process_one guards its own known failure modes, but this
                # thread is the SOLE capture worker: any unanticipated escape
                # (a counter sink fault, a replacement-connection path raising)
                # would kill capture silently for the daemon's whole lifetime.
                try:
                    conn = _process_one(conn, envelope)
                except BaseException:
                    _cnt_inc(_CNT_WORKER_ITEM_ERRORS)
                    _rate_limited_warning("family_book_telemetry: worker item failed")
                global _queue_high_water
                size = _obs_queue.qsize()
                if size > _queue_high_water:
                    _queue_high_water = size
                    _cnt_inc(_CNT_QUEUE_HIGH_WATER)
            finally:
                _obs_queue.task_done()
    finally:
        conn.close()


class CanonicalSeedUnavailableError(RuntimeError):
    """Raised by ``_bootstrap_last_state_cache`` when the durable canonical
    seed read did NOT positively complete: the canonical connection could not
    be opened, or the read failed for a reason other than a genuinely fresh,
    table-free schema. Callers must treat this as cache_seeded=False --
    swallowing it and reporting seeded=True regardless (the prior HIGH-2
    defect) mislabels the first observation of every family STATE_CHANGE
    without the operator ever finding out the seed never ran."""


def _bootstrap_last_state_cache(spool_conn: sqlite3.Connection) -> None:
    """Y4: seed from max(canonical durable, PENDING spool) per family -- a
    spool write that crashed before canonical ingestion must still be
    respected, or a restart mislabels unchanged content STATE_CHANGE.

    Canonical read never writes or issues DDL (Y2). HIGH-2: whether it
    actually COMPLETED is load-bearing for cache_seeded, so this function
    raises ``CanonicalSeedUnavailableError`` instead of swallowing that
    outcome. Only a positively-completed read (rows fetched, however many)
    or a positively-identified fresh-empty-schema ("no such table" -- there
    is nothing to seed, so that case IS fully seeded) return normally.
    """
    try:
        for family_id, (state_id, decision_time_iso) in _outbox_latest_per_family(spool_conn).items():
            _merge_cache_candidate(family_id, state_id, decision_time_iso)
    except Exception:
        # Best-effort auxiliary seed source. The spool connection was already
        # proven open and schema-armed by _open_spool, so a failure here is
        # unexpected -- but it must not block the canonical seed attempt
        # below, and it is not itself a canonical-seed-unavailable outcome.
        logger.warning("family_book_telemetry: spool-side seed read failed", exc_info=True)

    try:
        conn = _readonly_canonical_conn_factory()
    except Exception as exc:
        raise CanonicalSeedUnavailableError(f"canonical connection open failed: {exc!r}") from exc
    try:
        try:
            rows = conn.execute(
                """
                SELECT family_id, state_id, MAX(decision_time) AS decision_time
                FROM family_book_observations GROUP BY family_id
                """
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return  # positively-identified fresh empty schema -- nothing to seed, fully seeded
            raise CanonicalSeedUnavailableError(f"canonical seed read failed: {exc!r}") from exc
        for family_id, state_id, decision_time_iso in rows:
            _merge_cache_candidate(family_id, state_id, decision_time_iso)
    finally:
        conn.close()


def _merge_cache_candidate(family_id: str, state_id: str, decision_time_iso: str) -> None:
    try:
        decision_time = datetime.fromisoformat(decision_time_iso)
    except (TypeError, ValueError):
        return
    current = _last_state_by_family.get(family_id)
    if current is None or decision_time > current[1]:
        _last_state_by_family[family_id] = (state_id, decision_time)


def _process_one(conn: sqlite3.Connection, envelope: ObservationEnvelope) -> sqlite3.Connection:
    """Write one envelope's outbox row with X1 transaction safety
    (_rollback_or_replace UNCHANGED from round-3 per reviewer confirmation)
    -- now targeting the bounded outbox table, not the canonical tables
    directly. The WHOLE body is wrapped so a malformed envelope (a bug
    upstream, not a DB fault) is quarantined -- counted and dropped -- rather
    than crashing the worker (round-4 Y4)."""
    try:
        row = _build_outbox_row(envelope)
    except Exception:
        _cnt_inc(_CNT_MALFORMED_ENVELOPE)
        _rate_limited_warning("family_book_telemetry: malformed envelope quarantined")
        return conn

    if row is None:  # sampled out -- nothing to write
        return conn

    # Round-6 Z2: FAIL CLOSED. Capture is optional; the live-money DB's disk is
    # not. If the pending backlog cannot be read, the backlog is unknown, and
    # admitting a write on an unknown backlog is exactly how an optional plane
    # consumes the money path's storage. Skip the write instead.
    try:
        _pending_count, pending_bytes = _outbox_pending_budget(conn)
    except Exception:
        _cnt_inc(_CNT_SPOOL_BUDGET_UNREADABLE)
        _rate_limited_warning("family_book_telemetry: spool budget unreadable, dropping capture")
        return conn
    if pending_bytes >= _spool_pending_budget_bytes:
        _cnt_inc(_CNT_SPOOL_BUDGET_EXCEEDED)
        return conn

    try:
        insert_outbox_row(conn, row)
        conn.commit()
    except BaseException:
        conn = _rollback_or_replace(conn, _spool_conn_factory)
        _cnt_inc(_CNT_WRITE_FAILURES)
        _rate_limited_warning("family_book_telemetry: spool write failed")
        return conn

    _cnt_inc(_CNT_WRITTEN_OUTBOX)
    _last_state_by_family[envelope.family_id] = (row["state_id"], envelope.decision_time)
    return conn


def _rollback_or_replace(
    conn: sqlite3.Connection, conn_factory: Callable[[], sqlite3.Connection]
) -> sqlite3.Connection:
    """X1, UNCHANGED (per reviewer confirmation): guarantee the returned
    connection is NOT mid-transaction. Round-4 MEDIUM: if the REPLACEMENT
    connection's own setup fails, close it (never leak a handle) and let the
    caller keep going with whatever it can -- never let setup failure escape
    and kill the worker."""
    try:
        conn.rollback()
        if not conn.in_transaction:
            return conn
    except Exception:
        pass
    _rate_limited_warning("family_book_telemetry: rollback failed, replacing connection")
    try:
        conn.close()
    except Exception:
        pass
    try:
        return _open_spool(conn_factory)
    except Exception:
        _rate_limited_warning("family_book_telemetry: replacement connection setup failed")
        # Nothing usable to return; the caller's next write attempt will
        # retry conn_factory() via THIS same path (a stub connection here
        # would just fail identically on first use, so surface the same
        # failure shape the caller already handles -- open one more time,
        # letting a genuine outage raise up to _worker_loop's per-item guard
        # rather than silently returning a half-built connection).
        return conn_factory()


def _build_outbox_row(envelope: ObservationEnvelope) -> Optional[dict]:
    """Pure -- no I/O. Returns None iff sampled out."""
    state_id, content_hash, canonical_payload = compute_state_identity(envelope)
    decision_time_iso = envelope.decision_time.isoformat()

    state_changed, heartbeat_due, sampling_reason = _sampling_decision(
        envelope.family_id, state_id, envelope.decision_time, envelope.pre_veto_selected
    )
    if sampling_reason is None:
        _cnt_inc(_CNT_SAMPLED_OUT)
        return None

    center_value, center_status = market_center_and_status(envelope)
    from src.events.idempotency import sha256_text

    return {
        "enqueued_at_utc": datetime.now(timezone.utc).isoformat(),
        "state_id": state_id,
        "content_hash": content_hash,
        "hash_version": 1,
        "topology_hash": envelope.topology_hash,
        "complete_book": int(envelope.complete_book),
        "canonical_payload": canonical_payload,
        "payload_schema_version": 1,
        "observation_id": sha256_text(f"{envelope.family_id}|{envelope.receipt_hash}|{decision_time_iso}"),
        "family_id": envelope.family_id,
        "city": envelope.city,
        "target_date": envelope.target_date,
        "temperature_metric": envelope.temperature_metric,
        "decision_id": envelope.decision_id,
        "receipt_hash": envelope.receipt_hash,
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
        "sampling_reason": sampling_reason,
        "state_changed": int(state_changed),
        "heartbeat_due": int(heartbeat_due),
        "pre_veto_selected": int(envelope.pre_veto_selected),
        "selected_bin_id": envelope.selected_bin_id,
        "selected_side": envelope.selected_side,
        "sampling_policy_version": SAMPLING_POLICY_VERSION,
        "capture_seam": "DECISION_PRODUCTION",
        "schema_version": _OBSERVATIONS_SCHEMA_VERSION,
    }


def _sampling_decision(
    family_id: str, state_id: str, decision_time: datetime, pre_veto_selected: bool
) -> tuple[bool, bool, Optional[str]]:
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


# ---------------------------------------------------------------------------
# Delivery: run_bounded_ingest -- called by the DAEMON's own scheduler job
# (src/main.py, get_trade_connection(write_class="live")), NEVER by this
# module's worker thread. Pure with respect to connection lifecycle: the
# caller owns and closes both connections.
# ---------------------------------------------------------------------------

def outbox_has_pending(spool_conn_factory: Optional[Callable[[], sqlite3.Connection]] = None) -> bool:
    """Round-6 Z1: is there anything at all to deliver? Answered against the
    PRIVATE spool only, so the caller can skip opening a canonical trade
    connection entirely on an idle tick -- which is the overwhelmingly common
    case. Unreadable spool reads as "nothing pending": the optional plane
    yields rather than reaching for the live-money DB on a guess."""
    factory = spool_conn_factory or _spool_conn_factory
    try:
        conn = _open_spool(factory)
    except Exception:
        return False
    try:
        count, _bytes = _outbox_pending_budget(conn)
        return count > 0
    except Exception:
        return False
    finally:
        conn.close()


def run_bounded_ingest(
    canonical_conn: sqlite3.Connection,
    spool_conn_factory: Optional[Callable[[], sqlite3.Connection]] = None,
    *,
    batch_size: int = _INGEST_BATCH_SIZE_DEFAULT,
    byte_budget: int = _INGEST_BYTE_BUDGET_DEFAULT,
) -> IngestOutcome:
    """ONE bounded outbox->canonical batch. Never raises -- every failure
    mode returns a typed ``IngestOutcome`` so the caller (an
    ``@_scheduler_job``-wrapped daemon job, or a test) can log/count without
    the ingest pass ever crashing its host thread.

    Protocol: read a bounded batch (row count AND byte budget) from the
    spool, ordered by ``spool_seq``; insert into canonical
    family_book_states/family_book_observations in ONE transaction (targeted
    upserts -- idempotent, so a crash-and-replay is always safe); COMMIT
    canonical; ONLY THEN delete that spool_seq range in a SEPARATE spool
    transaction. Skips entirely (returns ``disabled=True``) if the kill
    switch is off -- the operator's emergency guard for canonical I/O, not
    merely new enqueues.

    DDL boundary: this function issues DDL against the SPOOL only (via
    ``_open_spool``, which owns schema + physical ceilings for every spool
    open in this module). It issues NO DDL against ``canonical_conn`` -- the
    trade schema bootstrap owns those tables, and a delivery pass must never
    mutate schema on a connection it does not own.
    """
    if not _telemetry_enabled():
        _cnt_inc(_CNT_INGEST_DISABLED)
        return IngestOutcome(0, 0, 0, disabled=True)

    factory = spool_conn_factory or _spool_conn_factory
    try:
        spool_conn = _open_spool(factory)
    except Exception as exc:
        _cnt_inc(_CNT_INGEST_FAILURES)
        return IngestOutcome(0, 0, 0, failed=True, reason=f"spool_connect: {exc!r}")

    try:
        try:
            rows = _outbox_fetch_batch(spool_conn, after_seq=0, limit=batch_size)
        except Exception as exc:
            _cnt_inc(_CNT_INGEST_FAILURES)
            return IngestOutcome(0, 0, 0, failed=True, reason=f"spool_read: {exc!r}")

        if not rows:
            return IngestOutcome(0, 0, 0)

        batch: list = []
        running_bytes = 0
        for r in rows:
            row_bytes = len(r["canonical_payload"]) + len(r["source_manifest_json"])
            if batch and running_bytes + row_bytes > byte_budget:
                break
            batch.append(r)
            running_bytes += row_bytes

        # Y2: NO DDL against canonical here, ever -- init_schema_trade_only
        # (the normal trade schema bootstrap) already created
        # family_book_states/family_book_observations before the daemon's
        # scheduler starts running jobs. If they are somehow still absent,
        # the INSERT calls below fail closed (caught, typed counter,
        # fail-soft) rather than this pass silently mutating schema on
        # someone else's connection.
        ingested_states = 0
        ingested_observations = 0
        try:
            for r in batch:
                if insert_state(
                    canonical_conn, state_id=r["state_id"], family_id=r["family_id"],
                    content_hash=r["content_hash"], hash_version=r["hash_version"],
                    topology_hash=r["topology_hash"], complete_book=bool(r["complete_book"]),
                    canonical_payload=r["canonical_payload"],
                    payload_schema_version=r["payload_schema_version"],
                    first_seen_decision_time=r["decision_time"],
                ):
                    ingested_states += 1
                if insert_observation(canonical_conn, dict(r)):
                    ingested_observations += 1
            canonical_conn.commit()
        except BaseException as exc:
            try:
                canonical_conn.rollback()
            except Exception:
                pass
            _cnt_inc(_CNT_INGEST_FAILURES)
            if "database or disk is full" in str(exc).lower():
                # The evidence DB's own max_page_count ceiling (or a genuine
                # disk-full condition) refused this write. Either way this
                # must be OBSERVABLE, not merely a generic ingest failure --
                # retention is an open operator decision (see PLAN.md) and
                # this counter is the signal that the ceiling was reached.
                _cnt_inc(_CNT_INGEST_EVIDENCE_DB_CEILING_HIT)
                logger.warning(
                    "family_book_telemetry: evidence DB write refused (physical "
                    "ceiling or disk full): %r", exc,
                )
            _rate_limited_warning("family_book_telemetry: canonical ingest failed", )
            return IngestOutcome(len(batch), 0, 0, failed=True, reason=repr(exc))

        # Counters published ONLY after a durable canonical commit (round-4 MEDIUM).
        if ingested_states:
            _cnt_inc(_CNT_INGESTED_STATES, delta=ingested_states)
        if ingested_observations:
            _cnt_inc(_CNT_INGESTED_OBSERVATIONS, delta=ingested_observations)
        _cnt_inc(_CNT_INGESTED_BATCHES)

        max_seq = max(int(r["spool_seq"]) for r in batch)
        try:
            _outbox_delete_up_to(spool_conn, max_seq)
            spool_conn.commit()
        except Exception as exc:
            # Canonical is already durable (idempotent upserts) -- a failed
            # ack just means the NEXT pass re-reads and re-no-ops these rows.
            # Round-6: SAFE is not SILENT. A persistently failing ack means the
            # outbox never drains while canonical ingestion keeps "succeeding",
            # so it gets its own counter and its own outcome field.
            try:
                spool_conn.rollback()
            except Exception:
                pass
            _cnt_inc(_CNT_ACK_FAILURES)
            _rate_limited_warning("family_book_telemetry: spool ack failed (safe -- will replay)")
            return IngestOutcome(
                len(batch), ingested_states, ingested_observations,
                ack_failed=True, reason=f"spool_ack: {exc!r}",
            )

        return IngestOutcome(len(batch), ingested_states, ingested_observations)
    finally:
        spool_conn.close()


def pending_outbox_stats(spool_conn_factory: Optional[Callable[[], sqlite3.Connection]] = None) -> dict:
    """Metrics: pending row count, approximate bytes, oldest enqueued_at_utc
    (Y1's required pending-rows/bytes/oldest-age observability)."""
    factory = spool_conn_factory or _spool_conn_factory
    conn = _open_spool(factory)
    try:
        return _outbox_pending_stats(conn)
    finally:
        conn.close()
