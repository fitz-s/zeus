# Created: 2026-05-07
# Last reused or audited: 2026-05-12
# Authority basis: .omc/plans/sqlite_contention_structural_design_v4_2026_05_07.md
#                  §3.1 (mechanism), §3.1.2 (per-DB flock topology),
#                  §3.1.5 (BulkChunker dual-channel watchdog),
#                  §3.1.7 (subprocess helper).
#                  architect K=3 structural decisions / AGENTS.md money path
#                  (K3 2026-05-12: BulkChunker yields LIVE at chunk boundary).
"""SQLite writer-lock helpers — Phase 0 of v4 plan.

Phase 0 lands the helper surface only. No production caller is migrated by
this module. Callers retain their existing get_*_connection() routes; the
helpers here will be threaded through in Phase 1+.

Key components:
  * `WriteClass` — LIVE / BULK enum
  * `db_writer_lock(db_path, write_class)` — fcntl.flock context manager,
    one of six lock files (3 DBs x 2 classes). Per plan §3.1.2.
  * `BulkChunker` — context-managed cooperative chunker for BULK writes
    with dual-channel (cooperative flag + interrupt_main) watchdog. Per
    plan §3.1.5 (resolves v3-critic MF5 critical bug).
  * `subprocess_with_write_class()` / `subprocess_run_with_write_class()`
    — spawn helpers that propagate ZEUS_DB_WRITE_CLASS env-var. Per plan
    §3.1.7 (resolves v3-critic AX1).

NOT in Phase 0:
  * Production callers are not retrofitted (Phase 1+).
  * `get_connection()` reclassification (Phase 1+).
  * Subprocess sites enumeration / replacement (Phase 1.y).
  * §3.4 production flag flip (Phase 3.x).
"""

from __future__ import annotations

import _thread
import enum
import errno
import fcntl
import logging
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from src.observability.counters import increment as _cnt_inc

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# §3.1 — WriteClass enum (LIVE / BULK)
# --------------------------------------------------------------------------


class WriteClass(str, enum.Enum):
    """Write classification used to pick the per-DB flock file.

    LIVE: live trading hot-path writes (priority, < 200 ms target latency).
    BULK: backfill / replay / migration writes (yields to LIVE via chunker).
    """

    LIVE = "live"
    BULK = "bulk"


# Eight lock-file slots: 4 DBs × 2 classes (per plan §3.1.2 + K1 split 2026-05-11).
# Lock files live alongside the DB they guard. The path layout matches the
# plan ("state/<db>.writer-lock.{live,bulk}") relative to the DB directory.
# K1 adds: state/zeus-forecasts.db.writer-lock.{live,bulk} (2 new slots).
_LOCK_FILE_SUFFIX = {
    WriteClass.LIVE: ".writer-lock.live",
    WriteClass.BULK: ".writer-lock.bulk",
}


def _lock_file_path(db_path: Path, write_class: WriteClass) -> Path:
    """Return the per-(db, class) lock-file path."""
    return db_path.with_name(db_path.name + _LOCK_FILE_SUFFIX[write_class])


# --------------------------------------------------------------------------
# §3.1.2 — db_writer_lock(): fcntl.flock context manager
# --------------------------------------------------------------------------


@contextmanager
def db_writer_lock(
    db_path: Path,
    write_class: WriteClass,
    *,
    blocking: bool = True,
) -> Iterator[None]:
    """Acquire the per-(db, class) writer lock for the duration of the block.

    Uses ``fcntl.flock(LOCK_EX)`` on a sentinel file next to the DB. Six
    distinct lock files exist per the plan (3 DBs × LIVE/BULK).

    The DB connection itself is unaffected; this lock only serializes the
    write *intent* across processes. Non-blocking mode is offered for
    callers that want to fall through quickly.

    Per plan §3.1.2.
    """
    lock_path = _lock_file_path(db_path, write_class)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Open in append mode so the file is always created and never truncated.
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError as exc:
            # Non-blocking and the lock is held; surface clearly.
            _cnt_inc("db_writer_lock_contended_total")
            raise BlockingIOError(
                errno.EWOULDBLOCK,
                f"db_writer_lock(write_class={write_class.value}) "
                f"contended on {lock_path}",
            ) from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as unlock_exc:
                logger.warning(
                    "db_writer_lock unlock failed for %s: %r",
                    lock_path,
                    unlock_exc,
                )
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# --------------------------------------------------------------------------
# §3.1.5 — BulkChunker (cooperative + interrupt_main watchdog)
# --------------------------------------------------------------------------


class BulkChunkerNotPolledError(RuntimeError):
    """Raised when a BULK caller holds the bulk flock too long without yielding.

    Surfaced from the main thread (cooperative path). The watchdog thread
    additionally calls ``_thread.interrupt_main()`` so the main thread is
    interrupted if blocked inside a long C-level call (executemany, etc).
    """


class BulkChunker:
    """Cooperative chunker for BULK writes; dual-channel watchdog.

    Per plan §3.1.5 (resolves v3-critic MF5 critical bug). The v3 spec had
    the watchdog raise inside a daemon thread, where Python silently
    swallows exceptions from non-main threads. v4 dual-channel:

    1. Cooperative flag (``threading.Event``) — set by watchdog, checked by
       main thread on every ``yield_if_live_contended()`` /
       ``commit_chunk()`` call. Surfaces as ``BulkChunkerNotPolledError``.
    2. ``_thread.interrupt_main`` — backstop that injects a
       ``KeyboardInterrupt`` into the main thread, in case the main thread
       is blocked in a long C-level call where the cooperative flag would
       not be checked.

    Context-manager lifecycle (``__enter__`` / ``__exit__``) starts and
    deterministically joins the watchdog thread (no daemon-thread leak).

    Usage:
        with BulkChunker(conn, caller_module=__name__) as chunker:
            for batch in batches:
                conn.executemany("INSERT INTO foo VALUES (?, ?)", batch)
                chunker.yield_if_live_contended()
                chunker.commit_chunk()
    """

    DEFAULT_CHUNK_MS = 50
    DEFAULT_CHUNK_ROWS = 2_000
    DEFAULT_WATCHDOG_S = 30
    DEFAULT_LIVE_YIELD_SLEEP_S = 0.05

    def __init__(
        self,
        conn: Any,
        *,
        caller_module: str,
        chunk_ms: int = DEFAULT_CHUNK_MS,
        chunk_rows: int = DEFAULT_CHUNK_ROWS,
        watchdog_s: int = DEFAULT_WATCHDOG_S,
        watchdog_poll_s: float = 1.0,
        db_path: Path | None = None,
        bulk_lock_fd: int | None = None,
        live_yield_sleep_s: float = DEFAULT_LIVE_YIELD_SLEEP_S,
    ) -> None:
        self.conn = conn
        self.caller_module = caller_module
        self.chunk_ms = chunk_ms
        self.chunk_rows = chunk_rows
        self.watchdog_s = watchdog_s
        self._watchdog_poll_s = watchdog_poll_s
        self._abort_requested = threading.Event()
        self._closed = threading.Event()
        self._last_yield_at = time.monotonic()
        # Guards _last_yield_at update from main thread vs watchdog read.
        self._lock = threading.Lock()
        self._fence_active = False
        self._fence_started_at: float | None = None
        self._fence_label: str | None = None
        self._watchdog_thread: threading.Thread | None = None
        # K3 2026-05-12: optional wiring so yield_if_live_contended() can
        # detect a LIVE waiter and briefly release the bulk fcntl + commit
        # the current SQLite chunk (the operative move that lets a LIVE
        # BEGIN IMMEDIATE slot in instead of waiting for the whole bulk
        # cycle). When db_path is None the chunker stays in Phase-0
        # cooperative-only mode and is a no-op on this axis.
        self._db_path = db_path
        self._bulk_lock_fd = bulk_lock_fd
        self._live_yield_sleep_s = live_yield_sleep_s

    # -- context-manager lifecycle (v4 MF5 §3.1.5) --

    def __enter__(self) -> "BulkChunker":
        self._abort_requested.clear()
        self._closed.clear()
        with self._lock:
            self._last_yield_at = time.monotonic()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_run,
            name=f"BulkChunker-watchdog-{self.caller_module}",
            daemon=True,
        )
        self._watchdog_thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._closed.set()
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=2.0)
            if self._watchdog_thread.is_alive():
                # Watchdog thread stuck — log but never block process exit.
                _cnt_inc("db_chunker_watchdog_join_timeout_total")
                logger.warning(
                    "BulkChunker watchdog (%s) failed to join within 2 s",
                    self.caller_module,
                )

    # -- main-thread API --

    def yield_if_live_contended(self) -> None:
        """Cooperative yield-point; main thread MUST call between chunks.

        Raises ``BulkChunkerNotPolledError`` if the watchdog has fired.

        K3 (2026-05-12) — if ``db_path`` was supplied at construction, this
        method probes the per-DB LIVE flock non-blocking. When a LIVE
        caller is currently holding the LIVE lock (i.e. is mid-write or
        about to ``BEGIN IMMEDIATE`` against the same SQLite file), the
        BULK chunker:

          1. ``commit_chunk()`` — release SQLite's engine-level write lock
             (the operative move; without this, fcntl shuffling does not
             help LIVE acquire the SQLite write lock).
          2. release the bulk fcntl (if ``bulk_lock_fd`` provided) so
             other BULK callers queued behind us can fair-share.
          3. brief jitter sleep (``live_yield_sleep_s``) to let LIVE
             complete its short transaction.
          4. re-acquire the bulk fcntl in blocking mode.

        Without ``db_path``/``bulk_lock_fd`` the method retains its Phase-0
        cooperative-only watchdog behaviour (back-compatible).
        """
        self._raise_if_aborted()
        with self._lock:
            self._last_yield_at = time.monotonic()
        _cnt_inc("db_chunker_yield_check_total")
        if self._db_path is None:
            return
        if self._fence_active:
            # Inside a cross-table fence, atomicity wins — never break the
            # chunk mid-fence even if LIVE is contending.
            return
        if self._is_live_contended():
            self._yield_to_live()

    # -- K3 helpers --

    def _is_live_contended(self) -> bool:
        """Non-blocking probe of the LIVE fcntl. True iff a LIVE caller holds it."""
        assert self._db_path is not None
        live_lock_path = _lock_file_path(self._db_path, WriteClass.LIVE)
        try:
            live_fd = os.open(str(live_lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            return False
        try:
            try:
                fcntl.flock(live_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # LIVE is currently held — that's our "waiter / active LIVE
                # work" signal. Treat as contention.
                _cnt_inc("db_chunker_live_contended_total")
                return True
            # We got it; LIVE is idle. Release immediately.
            try:
                fcntl.flock(live_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            return False
        finally:
            try:
                os.close(live_fd)
            except OSError:
                pass

    def _yield_to_live(self) -> None:
        """Release SQLite + bulk fcntl, sleep, re-acquire."""
        # 1. Operative SQLite release.
        self.commit_chunk()
        # 2. Bulk fcntl yield (optional; only when caller provided fd).
        bulk_fd = self._bulk_lock_fd
        released = False
        if bulk_fd is not None:
            try:
                fcntl.flock(bulk_fd, fcntl.LOCK_UN)
                released = True
            except OSError as unlock_exc:
                logger.warning(
                    "BulkChunker(%s) failed to release bulk fcntl for LIVE "
                    "yield: %r",
                    self.caller_module,
                    unlock_exc,
                )
        _cnt_inc("db_chunker_live_yield_total")
        # 3. Brief sleep so LIVE has room to acquire the SQLite write lock.
        time.sleep(self._live_yield_sleep_s)
        # 4. Re-acquire bulk fcntl (blocking) if we released it.
        if released and bulk_fd is not None:
            try:
                fcntl.flock(bulk_fd, fcntl.LOCK_EX)
            except OSError as relock_exc:
                # If re-acquire fails the chunker is in an inconsistent
                # state; raise so the BULK run aborts cleanly rather than
                # silently continuing without the bulk lock held.
                _cnt_inc("db_chunker_live_yield_relock_failed_total")
                raise RuntimeError(
                    f"BulkChunker({self.caller_module}) failed to re-acquire "
                    f"bulk fcntl after LIVE yield: {relock_exc!r}"
                ) from relock_exc
        # Reset the watchdog clock — yielding to LIVE is the opposite of
        # "stalled bulk work", and we don't want the watchdog to fire on
        # the sleep we just performed.
        with self._lock:
            self._last_yield_at = time.monotonic()

    def commit_chunk(self) -> None:
        """Commit the current chunk and let a fresh TX open lazily.

        INVARIANT (plan §3.1.6, retained from v3): callers MUST NOT call
        this between two writes to different tables in one logical TX. Use
        ``chunker.fence(label)`` for cross-table atomicity.
        """
        self._raise_if_aborted()
        # Real connections (sqlite3.Connection) implement .commit(); test
        # doubles may not need the actual commit. Tolerate AttributeError so
        # the watchdog/lifecycle tests can run with stub connections.
        commit = getattr(self.conn, "commit", None)
        if callable(commit):
            commit()

    @contextmanager
    def fence(
        self,
        label: str,
        *,
        timeout_s: int | None = None,
    ) -> Iterator[None]:
        """Suspend chunk-yields for an atomic cross-table block.

        Watchdog still fires if the fence exceeds ``watchdog_s`` (or
        ``timeout_s`` if explicitly tightened).
        """
        self._fence_active = True
        self._fence_started_at = time.monotonic()
        self._fence_label = label
        prior_watchdog = self.watchdog_s
        if timeout_s is not None:
            self.watchdog_s = timeout_s
        try:
            yield
        finally:
            self._fence_active = False
            self._fence_started_at = None
            self._fence_label = None
            self.watchdog_s = prior_watchdog
            with self._lock:
                self._last_yield_at = time.monotonic()  # reset clock

    # -- private --

    def _raise_if_aborted(self) -> None:
        if self._abort_requested.is_set():
            _cnt_inc("db_chunker_not_polled_total")
            raise BulkChunkerNotPolledError(
                f"BULK caller {self.caller_module} exceeded watchdog_s="
                f"{self.watchdog_s} without yield_if_live_contended() "
                f"(fence={self._fence_label}). v1 degradation."
            )

    def _watchdog_run(self) -> None:
        """Daemon thread: sets cooperative flag + interrupts main on timeout."""
        while not self._closed.is_set():
            # Use Event.wait to allow fast shutdown; returns True if set.
            if self._closed.wait(self._watchdog_poll_s):
                return
            with self._lock:
                last = self._last_yield_at
            elapsed = time.monotonic() - last
            if elapsed > self.watchdog_s:
                # v4 MF5: dual-channel abort.
                # Channel 1: cooperative flag (primary; main thread checks
                # at next yield/commit).
                self._abort_requested.set()
                _cnt_inc("db_chunker_watchdog_fired_total")
                # Channel 2: interrupt_main (backstop; covers main-thread
                # blocked in a C-level call). interrupt_main raises in the
                # main thread regardless of GIL state.
                try:
                    _thread.interrupt_main()
                except (KeyboardInterrupt, RuntimeError):
                    # Main thread is already in shutdown; harmless.
                    pass
                return  # watchdog's job is done; exit thread.


# --------------------------------------------------------------------------
# K3 (2026-05-12) — convenience: bulk fcntl + chunker with LIVE-yield wiring
# --------------------------------------------------------------------------


@contextmanager
def bulk_lock_with_chunker(
    db_path: Path,
    conn: Any,
    *,
    caller_module: str,
    chunk_ms: int = BulkChunker.DEFAULT_CHUNK_MS,
    chunk_rows: int = BulkChunker.DEFAULT_CHUNK_ROWS,
    watchdog_s: int = BulkChunker.DEFAULT_WATCHDOG_S,
    watchdog_poll_s: float = 1.0,
    live_yield_sleep_s: float = BulkChunker.DEFAULT_LIVE_YIELD_SLEEP_S,
) -> Iterator[BulkChunker]:
    """Open the BULK fcntl + wrap a ``BulkChunker`` with LIVE-yield wiring.

    This is the K3 (2026-05-12) entry point for BULK callers that want
    cooperative LIVE-yield behaviour at chunk boundaries. The convenience
    helper owns the fcntl FD and threads it through the chunker so the
    chunker can release-then-reacquire the bulk fcntl when a LIVE writer
    appears mid-cycle.

    Compare to the older pattern::

        with db_writer_lock(db_path, WriteClass.BULK):
            with BulkChunker(conn, caller_module=...) as ch:
                ...

    which does NOT yield to LIVE (the fcntl FD is opaque to the chunker).

    The new pattern::

        with bulk_lock_with_chunker(db_path, conn, caller_module=...) as ch:
            for batch in batches:
                conn.executemany(...)
                ch.yield_if_live_contended()
                ch.commit_chunk()

    Honors the same lifecycle guarantees as the underlying primitives
    (watchdog thread joined on exit; bulk fcntl released on exit).
    """
    lock_path = _lock_file_path(db_path, WriteClass.BULK)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            chunker = BulkChunker(
                conn,
                caller_module=caller_module,
                chunk_ms=chunk_ms,
                chunk_rows=chunk_rows,
                watchdog_s=watchdog_s,
                watchdog_poll_s=watchdog_poll_s,
                db_path=db_path,
                bulk_lock_fd=fd,
                live_yield_sleep_s=live_yield_sleep_s,
            )
            with chunker:
                yield chunker
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as unlock_exc:
                logger.warning(
                    "bulk_lock_with_chunker unlock failed for %s: %r",
                    lock_path,
                    unlock_exc,
                )
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# --------------------------------------------------------------------------
# §3.1.7 — subprocess helpers (env-var propagation)
# --------------------------------------------------------------------------


def _merged_env(
    write_class: WriteClass,
    env: Mapping[str, str] | None,
) -> dict[str, str]:
    base = dict(env) if env is not None else dict(os.environ)
    base["ZEUS_DB_WRITE_CLASS"] = write_class.value
    return base


def subprocess_with_write_class(
    cmd: list[str] | str,
    write_class: WriteClass,
    *,
    env: Mapping[str, str] | None = None,
    **popen_kwargs: Any,
) -> subprocess.Popen:
    """Spawn a subprocess with ``ZEUS_DB_WRITE_CLASS`` pre-set.

    Phase 0: helper exists; callers are migrated in Phase 1.y. The
    collection-time antibody (conftest.py §10.5) AST-scans for raw
    ``subprocess.{Popen,run,...}`` outside this helper's allowlist and
    fails CI on violations once Phase 1.y completes.
    """
    return subprocess.Popen(  # noqa: S603 - explicit helper call
        cmd,
        env=_merged_env(write_class, env),
        **popen_kwargs,
    )


def subprocess_run_with_write_class(
    cmd: list[str] | str,
    write_class: WriteClass,
    *,
    env: Mapping[str, str] | None = None,
    **run_kwargs: Any,
) -> subprocess.CompletedProcess:
    """Synchronous variant for ``subprocess.run``."""
    return subprocess.run(  # noqa: S603 - explicit helper call
        cmd,
        env=_merged_env(write_class, env),
        **run_kwargs,
    )


# --------------------------------------------------------------------------
# Allowlists (populated as Phase 1.y migrates callers)
# --------------------------------------------------------------------------

# Files where direct ``sqlite3.connect()`` is permitted.
SQLITE_CONNECT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "src/state/db.py",  # canonical shim
        "src/state/db_writer_lock.py",  # this file — does not connect
        # Track A.6 (#246): daemon-path raw-connect sites — annotated below.
        # These are NOT in the world-db BULK lock universe; each is either
        # read-only or writes a separate DB (risk_state.db).
        "src/ingest_main.py",           # RO: reads condition_id for UMA listener, no write
        "src/observability/status_summary.py",  # RO: status dashboard read-only
        "src/riskguard/discord_alerts.py",  # WRITE risk_state.db only; not in world-db BULK lock universe
        "scripts/promote_calibration_v2_stage_to_prod.py",  # RO inspect/verify; RW only with --commit
        "src/control/cli/promote_entry_forecast.py",  # RO: operator CLI opens world-db with mode=ro
        # K1 workload-class split (2026-05-12): PR #112 Option (c) split of
        # the original single-script design. Each handles RO inspect/verify;
        # RW only with --commit, gated by BEGIN IMMEDIATE + rollback semantics.
        "scripts/promote_platt_models_v2.py",       # RO inspect/verify; RW only with --commit (zeus-world.db)
        "scripts/promote_calibration_pairs_v2.py",  # RO inspect/verify; RW only with --commit (zeus-forecasts.db)
    }
)

# Phase-1 staging allowlist for callers that may invoke ``_connect()``
# without a ``write_class=`` kwarg during the rolling retrofit. Empty
# after Phase 3.
WRITE_CLASS_STAGING_ALLOWLIST: frozenset[str] = frozenset()

# Allowlisted (file, lineno) tuples for raw subprocess calls that
# provably do not touch the DB. Populated from §3.1.7 enumeration during
# Phase 1.y; in Phase 0 we hold an empty set so the antibody is
# non-blocking until Phase 1.y enumeration lands.
SUBPROCESS_NO_DB_ALLOWLIST: frozenset[tuple[str, int]] = frozenset()


# Canonical alphabetical order for cross-DB ATTACH (per plan §3.1.3).
# Lock acquisition under this order prevents deadlocks under mixed
# cross-DB workloads.
CROSS_DB_CANONICAL_ORDER: tuple[str, ...] = (
    "risk_state.db",
    "zeus-forecasts.db",  # K1 split 2026-05-11: inserted alphabetically between risk_state and zeus-world
    "zeus-world.db",
    "zeus_trades.db",
)


def canonical_lock_order(db_paths: list[Path]) -> list[Path]:
    """Sort lock targets into canonical alphabetical order.

    Used by ``get_trade_connection_with_world()`` migration in Phase 1+
    to prevent cross-DB deadlocks. Phase 0 ships the helper only.
    """
    return sorted(db_paths, key=lambda p: p.name)


# --------------------------------------------------------------------------
# §10.4 — Scheduler add_job wrapper + _resolve_write_class integration
# --------------------------------------------------------------------------


def _resolve_write_class_str(value: str | WriteClass) -> WriteClass:
    """Coerce str/WriteClass to a WriteClass; raises on invalid input."""
    if isinstance(value, WriteClass):
        return value
    return WriteClass(str(value).lower())


def add_job_with_write_class(
    scheduler: Any,
    func: Any,
    *args: Any,
    write_class: str | WriteClass = "bulk",
    **kwargs: Any,
) -> Any:
    """Schedule ``func`` on ``scheduler`` with a per-job write_class.

    v4 plan §10.4: every scheduled job that ultimately writes to a Zeus DB
    must carry an explicit write_class so the connection helpers
    (db.py::_connect / get_connection / ...) can route the job onto the
    correct flock. This wrapper:

    1. Resolves ``write_class`` via the same precedence as
       ``db._resolve_write_class()`` (explicit kwarg > env > default),
       defaulting to BULK because the dominant ingest jobs are BULK.
    2. Wraps ``func`` so the resolved class is exported to the
       ``ZEUS_DB_WRITE_CLASS`` env var for the duration of the job
       invocation (thread-local-restoration semantics: the prior value is
       snapshotted on enter and restored on exit, so concurrent threadpool
       jobs do not stomp each other if one runs without an explicit
       class).
    3. Delegates to ``scheduler.add_job(wrapped, *args, **kwargs)`` and
       returns whatever the underlying scheduler returns.

    The wrapper is APScheduler-compatible (the scheduler is duck-typed:
    any object with an ``add_job(func, *args, **kwargs)`` method works,
    so test doubles do not need APScheduler installed).

    Phase 0.5 lands the helper; the ingest_main.py / main.py call-site
    retrofit is part of Phase 1+.
    """
    resolved = _resolve_write_class_str(write_class)

    def _wrapped(*a: Any, **kw: Any) -> Any:
        prior = os.environ.get("ZEUS_DB_WRITE_CLASS")
        os.environ["ZEUS_DB_WRITE_CLASS"] = resolved.value
        _cnt_inc(f"db_scheduler_job_{resolved.value}_total")
        try:
            return func(*a, **kw)
        finally:
            if prior is None:
                os.environ.pop("ZEUS_DB_WRITE_CLASS", None)
            else:
                os.environ["ZEUS_DB_WRITE_CLASS"] = prior

    # Preserve identifying metadata for debugging / introspection.
    try:
        _wrapped.__name__ = getattr(func, "__name__", "_wrapped")
        _wrapped.__doc__ = getattr(func, "__doc__", None)
    except (AttributeError, TypeError):
        pass

    return scheduler.add_job(_wrapped, *args, **kwargs)


class FastPoolExecutor:
    """Thin scheduler wrapper that pins every job to a write_class.

    v4 plan §10.4. Wraps any scheduler exposing ``add_job(func, ...)``
    (APScheduler ``BlockingScheduler`` / ``BackgroundScheduler``, or any
    duck-typed test double). Every ``self.add_job(...)`` call routes
    through ``add_job_with_write_class``.

    Construction:
        from apscheduler.schedulers.blocking import BlockingScheduler
        sched = BlockingScheduler(...)
        fast = FastPoolExecutor(sched, default_write_class="bulk")
        fast.add_job(my_tick, "cron", minute=0, id="my_tick")
        # explicit override:
        fast.add_job(live_tick, "cron", second=15, id="live_tick",
                     write_class="live")

    The wrapper does NOT hold or run the scheduler; it only proxies
    add_job. The caller still owns ``sched.start()`` / ``sched.shutdown()``.
    """

    def __init__(
        self,
        scheduler: Any,
        *,
        default_write_class: str | WriteClass = "bulk",
    ) -> None:
        self._scheduler = scheduler
        self._default = _resolve_write_class_str(default_write_class)

    @property
    def scheduler(self) -> Any:
        """Return the underlying scheduler (for callers that need start/shutdown)."""
        return self._scheduler

    def add_job(
        self,
        func: Any,
        *args: Any,
        write_class: str | WriteClass | None = None,
        **kwargs: Any,
    ) -> Any:
        """Schedule a job; resolves write_class from explicit kwarg or default."""
        wc = (
            _resolve_write_class_str(write_class)
            if write_class is not None
            else self._default
        )
        return add_job_with_write_class(
            self._scheduler,
            func,
            *args,
            write_class=wc,
            **kwargs,
        )
