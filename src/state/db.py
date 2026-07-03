# Created: prior to 2026-04-26
# Last reused or audited: 2026-06-08
# Authority basis: Zeus DB schema + world_write_mutex CATEGORY ANTIBODY.
#   2026-06-08 thepath/audit-realign Fitz #5 lock-CATEGORY kill: _apply_busy_timeout
#   helper + SQL-level PRAGMA busy_timeout in _connect()/get_connection() so a
#   factory handle's wait budget is durable (un-strippable by executescript) — a
#   writer that loses the WAL write lock WAITS, not raises "database is locked".
#   Connection PRAGMA only; INV-37 ATTACH+SAVEPOINT + txn semantics unchanged.
#   2026-06-04 Phase 1: thread-local held depth (_GuardedWorldMutex) — eliminates
#   cross-thread false positives that caused background venue I/O to trip the guard
#   (283× advisory/2min in prod → WAL re-bloat). Phase 2: guard armed fatal (always
#   raises, ZEUS_WORLD_MUTEX_IO_ADVISORY=1 to downgrade in emergency).
#   Plan: architecture/world_mutex_io_offmutex_refactor_2026_06_04.md
"""Zeus database schema and connection management.

All tables enforce the 4-timestamp constraint where applicable.
Settlement truth = Polymarket settlement result (spec §1.3).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional

if TYPE_CHECKING:
    from src.state.db_writer_lock import WriteClass

from src.architecture.decorators import capability
from src.config import STATE_DIR, get_mode, state_path


def utc_iso_now() -> str:
    """Return the current UTC instant as an ISO-8601 string with timezone offset.

    Single canonical producer for caller-supplied tz-aware timestamps (ANTIBODY 2).
    Use this instead of datetime.now() or CURRENT_TIMESTAMP for any persisted timing
    column that must compare correctly against tz-aware datetimes on the Chicago host.

    Returns strings of the form: '2026-06-16T12:34:56.789012+00:00'
    """
    return datetime.now(timezone.utc).isoformat()
from src.contracts.semantic_types import ExitState
from src.contracts.freshness_registry import FreshnessLevel, registry as _freshness_registry
from src.state.ledger import (
    CANONICAL_POSITION_EVENT_COLUMNS,
    _ensure_position_current_authority_columns,
    apply_architecture_kernel_schema,
    append_many_and_project,
)
from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS, POSITION_EVENT_ENVS
from src.state.collateral_ledger import init_collateral_schema
from src.state.market_topology_repo import write_market_topology_state
from src.state.snapshot_repo import init_snapshot_schema
from src.observability.counters import increment as _cnt_inc


ZEUS_DB_PATH = STATE_DIR / "zeus.db"  # LEGACY — remove after Phase 4
ZEUS_WORLD_DB_PATH = STATE_DIR / "zeus-world.db"  # Shared world data (settlements, calibration, ENS)
ZEUS_FORECASTS_DB_PATH = STATE_DIR / "zeus-forecasts.db"  # K1 split 2026-05-11: forecast/harvester-truth class
ZEUS_BACKTEST_DB_PATH = STATE_DIR / "zeus_backtest.db"  # Derived audit output; never runtime authority
RISK_DB_PATH = STATE_DIR / "risk_state.db"  # Single risk DB (live-only)

CANONICAL_STRATEGY_KEYS = frozenset(
    {
        "settlement_capture",
        "shoulder_sell",
        "center_buy",
        "opening_inertia",
    }
)

_EXIT_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "EXIT_ORDER_POSTED",
        "EXIT_INTENT",
        "EXIT_ORDER_ATTEMPTED",
        "EXIT_ORDER_FILLED",
        "EXIT_ORDER_REJECTED",
        "EXIT_ORDER_VOIDED",
        "EXIT_RETRY_SCHEDULED",
        "EXIT_BACKOFF_EXHAUSTED",
        "EXIT_INTENT_RECOVERED",
        "EXIT_FILL_CONFIRMED",
        "EXIT_FILL_CHECKED",
        "EXIT_FILL_CHECK_FAILED",
        "EXIT_RETRY_RELEASED",
        "EXIT_ORDER_ID_MISSING",
    }
)
_EXIT_STATE_HINT_VALUES = frozenset(state.value for state in ExitState if state.value)
_TRANSITIONAL_HINT_EVENT_TYPES = frozenset(
    {
        "POSITION_OPEN_INTENT",
        "ENTRY_ORDER_POSTED",
        "ENTRY_ORDER_FILLED",
        "DAY0_WINDOW_ENTERED",
        "ADMIN_VOIDED",
        "EXIT_RETRY_RELEASED",
        *_EXIT_LIFECYCLE_EVENT_TYPES,
    }
)
_TRANSITIONAL_HINT_PAYLOAD_KEYS = (
    "entry_fill_verified",
    "admin_exit_reason",
    "day0_entered_at",
)
_TRANSITIONAL_HINT_ROWS_PER_POSITION = 40

# T1E: configurable busy-timeout (ms → s). Default 30000ms = 30s per T0_SQLITE_POLICY.md.
# ZEUS_DB_BUSY_TIMEOUT_MS env var is in milliseconds; sqlite3.connect(timeout=) takes seconds.
# Malformed value falls back to default (catch-and-log) so daemon never crashes on bad config.
def _db_busy_timeout_s() -> float:
    """Return sqlite3 busy-timeout in seconds from ZEUS_DB_BUSY_TIMEOUT_MS env var.

    Reads env var on each call so long-running daemons pick up runtime changes.
    Default: 30000 ms (30 s) per T0_SQLITE_POLICY.md.
    """
    raw = os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000")
    try:
        ms = float(raw)
    except (ValueError, TypeError):
        _startup_logger = logging.getLogger(__name__)
        _startup_logger.warning(
            "ZEUS_DB_BUSY_TIMEOUT_MS=%r is not a valid number; "
            "falling back to default 30000 ms (30 s)",
            raw,
        )
        return 30.0
    # T2F-NEGATIVE-ENV-VALIDATION-LOUD-FAIL: reject negative values at parse
    # time so a misconfigured daemon fails loudly rather than silently using a
    # negative sqlite3 timeout (which may behave as an indefinite lock).
    if ms < 0:
        raise ValueError(
            f"ZEUS_DB_BUSY_TIMEOUT_MS must be >= 0; got {raw!r} ({ms} ms). "
            "Fix the environment variable before starting the daemon."
        )
    return ms / 1000.0


def _db_busy_timeout_ms() -> int:
    """Return the configured busy-timeout in MILLISECONDS for PRAGMA busy_timeout.

    Mirrors ``_db_busy_timeout_s`` (same env var, same default, same negative-value
    rejection) but in the unit PRAGMA busy_timeout expects. Kept as the single
    integer-ms source so the factory's SQL-level wait budget can never drift from
    the connect-time ``timeout=`` seconds value.
    """
    return int(round(_db_busy_timeout_s() * 1000.0))


def _apply_busy_timeout(conn: sqlite3.Connection) -> None:
    """Set ``PRAGMA busy_timeout`` at the SQL level on ``conn``.

    CATEGORY ANTIBODY (Fitz #5 — make "database is locked" unconstructable):
    ``sqlite3.connect(timeout=N)`` installs only a C-level busy handler, which
    some Python/SQLite builds NULL on the first ``executescript()`` (init_schema,
    init_risk_db, any schema-ensure), dropping the wait budget to 0 ms so the
    next write fails INSTANTLY with "database is locked" instead of waiting. The
    only durable fix is to set the budget at the SQL level here AND re-apply it
    after every executescript that hands the connection back. The value is
    normalized to int before interpolation (PRAGMA forbids bound parameters), so
    no untrusted text can enter the statement.

    Behavior-preserving: this only WIDENS the wait budget; it never changes
    transaction semantics, write ordering, or the ATTACH+SAVEPOINT cross-DB path
    (INV-37). It is a pure connection PRAGMA.
    """
    busy_ms = _db_busy_timeout_ms()
    conn.execute("PRAGMA busy_timeout = %d" % busy_ms)


def _zeus_trade_db_path() -> Path:
    """Physical path for the trade database."""
    return STATE_DIR / "zeus_trades.db"


def _resolve_write_class(
    explicit: WriteClass | str | None = None,
) -> "WriteClass | None":
    """Resolve the WriteClass for a connection.

    Order: explicit kwarg > ZEUS_DB_WRITE_CLASS env var > None (Phase 0.5
    helper surface; callers retrofit in Phase 1+). Returns None when the
    caller has not opted in — the connection is opened without a flock,
    matching pre-v4 behavior.

    Per v4 plan §AX3 + §10.4.
    """
    from src.state.db_writer_lock import WriteClass  # local import: avoid cycle
    if explicit is None:
        env_val = os.environ.get("ZEUS_DB_WRITE_CLASS")
        if env_val is None:
            return None
        try:
            return WriteClass(env_val.lower())
        except ValueError:
            logger.warning(
                "ZEUS_DB_WRITE_CLASS=%r is not a valid WriteClass; ignoring",
                env_val,
            )
            return None
    if isinstance(explicit, str):
        return WriteClass(explicit.lower())
    return explicit


def _connect(
    db_path: Path,
    *,
    write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Low-level connection with standard pragmas.

    Phase 0.5: ``write_class`` kwarg is accepted for caller classification
    (v4 plan §3.1, §AX3). When None and ``ZEUS_DB_WRITE_CLASS`` env var is
    unset, behavior is identical to pre-v4. When set (explicit or env), the
    class is recorded via counter; flock acquisition is reserved for
    Phase 1+ retrofits where callers wrap the connection lifetime in
    ``db_writer_lock(...)`` themselves.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # T1E: timeout read from ZEUS_DB_BUSY_TIMEOUT_MS env var (ms→s); default 30s.
    timeout_s = _db_busy_timeout_s()
    conn = sqlite3.connect(str(db_path), timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # 2026-05-12 antibody (cold-cache K3 partial fix): bump page cache to 1 GB
    # so the hot working set of large forecast tables stays resident across
    # cycles. Default is ~2 MB which is fatal for 35 GB forecasts.db cold-cache
    # B-tree descent. ZEUS_DB_CACHE_KB env var overrides; -1048576 = 1 GiB.
    # Per-connection, so independent for trade/world/forecasts/backtest.
    cache_kb = int(os.environ.get("ZEUS_DB_CACHE_KB", "1048576"))
    conn.execute(f"PRAGMA cache_size = -{cache_kb}")
    # 2026-05-13 antibody (cold-cache K3 follow-up): mmap_size lets SQLite use
    # OS-managed page cache instead of bounded per-connection cache. On the
    # post-promote 51 GB forecasts.db, 1 GB cache_size still thrashes when
    # the working set (2945 distinct ingest tuples × 5-column autoindex
    # descent) exceeds it. Setting mmap_size to a 32 GB ceiling lets the OS
    # cache reused autoindex/data pages across queries WITHOUT churning the
    # SQLite cache. ZEUS_DB_MMAP_BYTES env var overrides.
    mmap_bytes = int(os.environ.get("ZEUS_DB_MMAP_BYTES", str(32 * 1024 * 1024 * 1024)))
    conn.execute(f"PRAGMA mmap_size = {mmap_bytes}")
    _install_connection_functions(conn)
    # CATEGORY ANTIBODY (Fitz #5): set the SQL-level wait budget so a writer that
    # loses the WAL write lock WAITS up to busy_timeout instead of raising
    # "database is locked" instantly. sqlite3.connect(timeout=) alone only sets a
    # C-level handler that executescript() can null; this PRAGMA is the durable
    # budget. Connection PRAGMA only — INV-37 / txn semantics unchanged.
    _apply_busy_timeout(conn)
    resolved = _resolve_write_class(write_class)
    if resolved is not None:
        _cnt_inc(f"db_connect_write_class_{resolved.value}_total")
    return conn


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    """Low-level read-only SQLite connection with bounded lock wait.

    Read-only helpers must not create the DB, run write-oriented pragmas, inherit
    the live writer timeout, or permit accidental mutation through a misleading
    connection name.
    """

    timeout_ms = int(os.environ.get("ZEUS_DB_READ_BUSY_TIMEOUT_MS", "1000"))
    conn = sqlite3.connect(
        f"file:{db_path.resolve()}?mode=ro",
        uri=True,
        timeout=max(0.001, timeout_ms / 1000.0),
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA foreign_keys=ON")
    cache_kb = int(os.environ.get("ZEUS_DB_CACHE_KB", "1048576"))
    conn.execute(f"PRAGMA cache_size = -{cache_kb}")
    mmap_bytes = int(os.environ.get("ZEUS_DB_MMAP_BYTES", str(32 * 1024 * 1024 * 1024)))
    conn.execute(f"PRAGMA mmap_size = {mmap_bytes}")
    _install_connection_functions(conn)
    conn.execute(f"PRAGMA busy_timeout = {timeout_ms}")
    return conn


def get_trade_connection(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Trade DB connection (zeus_trades.db)."""
    return _connect(_zeus_trade_db_path(), write_class=write_class)


def get_trade_connection_read_only() -> sqlite3.Connection:
    """Read-only trade DB connection (write_class=None)."""
    return _connect_read_only(_zeus_trade_db_path())


def get_world_connection(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Shared world data DB (settlements, calibration, ENS)."""
    return _connect(ZEUS_WORLD_DB_PATH, write_class=write_class)


def get_forecasts_connection(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Forecast/harvester-truth co-transactional class DB (zeus-forecasts.db).

    Owns: ensemble_snapshots, source_run, source_run_coverage,
          producer readiness_state, job_run for forecast-live work,
          observations, settlement_outcomes, market_events,
          calibration_pairs.
    Lock files: state/zeus-forecasts.db.writer-lock.{bulk,live}.

    K1 split 2026-05-11: physically separate flock from zeus-world.db so
    BULK forecast ingest cannot starve LIVE/world writes (K1 contention fix).
    """
    return _connect(ZEUS_FORECASTS_DB_PATH, write_class=write_class)


def get_world_connection_read_only() -> sqlite3.Connection:
    """Read-only world DB connection (write_class=None).
    T1 thin wrapper — encodes read-only intent in the call site name.
    INV-37: single-DB read; no ATTACH path.
    """
    return _connect_read_only(ZEUS_WORLD_DB_PATH)


def get_forecasts_connection_read_only() -> sqlite3.Connection:
    """Read-only forecasts DB connection (write_class=None).
    T1 thin wrapper — encodes read-only intent in the call site name.
    INV-37: single-DB read; no ATTACH path.
    """
    return _connect_read_only(ZEUS_FORECASTS_DB_PATH)


# --------------------------------------------------------------------------
# zeus-world.db IN-PROCESS WRITE SERIALIZATION (2026-05-31)
# --------------------------------------------------------------------------
# Root (EDLI live canary): zeus-world.db is a WAL database with multiple
# in-process writers running as apscheduler jobs / daemon threads inside the
# SAME daemon process — especially the EDLI reactor (EventStore emit/claim/mark)
# and other world-class event writers. Market-channel executable feasibility rows
# are trade-class evidence and are not written through this world lock. With
# sqlite3's default ``isolation_level=""`` (implicit DEFERRED BEGIN), the first
# DML opens a transaction that upgrades to the single WAL *write* lock and holds
# it until COMMIT. The reactor's long cycle (~330 s, incl. HTTP/MC re-pricing)
# left that write lock held for the whole cycle, so the ingestor thread blocked
# the full 30 s busy_timeout on every write → "database is locked" → the reactor
# cycle hung/skipped (status=FAILED, no completed cycle).
#
# Fix (textbook, in-process): because every writer runs in ONE process, a single
# process-global ``threading.Lock`` around each world-DB WRITE *transaction*
# serializes them so SQLite never sees two concurrent writers → no WAL write-lock
# starvation. WAL still allows concurrent READERS, so reads are NOT taken through
# this lock. The lock is held ONLY around the DB txn (BEGIN IMMEDIATE → writes →
# COMMIT), kept short, and released on exception (rollback-then-release) so a
# failing writer never holds it. It MUST NOT be held across network/HTTP calls.
#
# This complements (does not replace) the cross-PROCESS fcntl flock in
# db_writer_lock.py: that serializes write *intent* across processes; this
# serializes write *transactions* across THREADS within one process. They are
# orthogonal and composable.
#
# CATEGORY ANTIBODY (2026-06-04, M5 lock-starvation kill — the K<<N structural
# fix): the docstring contract "MUST NOT be held across network/HTTP calls" was
# UNENFORCED. STEP-7 (5638cf59c6) and #95 each fixed ONE site by hand; M5
# exchange_reconcile was an uncovered third instance and wedged the daemon for
# hours (STAT=U, WAL bloat). A by-hand fix per site is whack-a-mole. Instead we
# make "blocking network/chain I/O while the world write lock is held"
# STRUCTURALLY DETECTABLE: the mutex is wrapped so every acquire()/release()
# (whether via world_write_lock, world_write_mutex().acquire(), or a bare
# context-manager use) maintains a THREAD-LOCAL held depth. The venue HTTP
# client + on-chain RPC entrypoints assert that THIS THREAD's depth is zero
# before doing I/O and raise WorldMutexIOViolation otherwise — converting a
# silent multi-hour wedge into an immediate, located failure at the exact
# violating call site.
#
# CRITICAL: the held counter MUST be thread-local (not process-global). Using a
# shared global would make background threads observing a DIFFERENT thread's
# mutex acquisition falsely fire the guard — background I/O is NOT a violation.
# The property we enforce is "this thread holds the mutex AND is about to do
# blocking I/O", NOT "some thread holds the mutex". threading.local() provides
# the correct per-thread isolation. (2026-06-04 P1 fix, commit: see git log.)


class WorldMutexIOViolation(RuntimeError):
    """Raised when blocking network/on-chain I/O is attempted while the
    process-global zeus-world.db write mutex is held.

    Holding the world write mutex (and the WAL write lock it guards) across a
    venue HTTP fetch or an on-chain RPC call serializes every world write behind
    that I/O and wedges the daemon (the M5 / #95 / STEP-7 starvation disease).
    The contract on ``world_write_lock`` / ``world_write_mutex`` is explicit:
    NEVER hold across HTTP. This exception makes the contract self-enforcing.
    """


def _world_live_writer_lock_path() -> Path:
    """Return the cross-process LIVE writer lock file for zeus-world.db."""

    return ZEUS_WORLD_DB_PATH.with_name(ZEUS_WORLD_DB_PATH.name + ".writer-lock.live")


def _lock_acquire_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[bool, float]:
    blocking = bool(kwargs.get("blocking", True))
    timeout = float(kwargs.get("timeout", -1.0))
    if args:
        blocking = bool(args[0])
    if len(args) >= 2:
        timeout = float(args[1])
    return blocking, timeout


def _acquire_flock_fd(lock_path: Path, *, blocking: bool, timeout: float) -> int | None:
    """Acquire a fcntl flock and return the open fd, or None for non-blocking miss."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    flags = fcntl.LOCK_EX
    if not blocking or timeout >= 0.0:
        flags |= fcntl.LOCK_NB
    deadline = time.monotonic() + timeout if timeout >= 0.0 else None
    while True:
        try:
            fcntl.flock(fd, flags)
            return fd
        except BlockingIOError:
            if not blocking or (deadline is not None and time.monotonic() >= deadline):
                os.close(fd)
                return None
            time.sleep(0.01)
        except OSError:
            os.close(fd)
            raise


class _GuardedWorldMutex:
    """A ``threading.Lock`` facade that tracks THREAD-LOCAL held depth.

    Wraps the real lock so that EVERY acquisition path — ``world_write_lock``
    (the BEGIN/COMMIT context manager), ``world_write_mutex().acquire()`` (the
    reactor / ingestor / emit direct-acquire sites), and ``with mutex:`` — feeds
    one thread-local held counter with ZERO caller changes. The counter is the
    single source of truth that ``assert_no_world_mutex_held_for_io`` reads.

    THREAD-LOCAL SEMANTICS (2026-06-04 P1 fix): the counter is stored in a
    ``threading.local()`` so it reflects only the CALLING THREAD'S acquisitions.
    A background thread doing venue I/O while a DIFFERENT thread holds the mutex
    must NOT be flagged — that is correct concurrent operation. The property we
    enforce is "this thread holds the world mutex AND is about to do blocking I/O",
    which is the actual WAL-starvation condition. A shared global counter caused
    background threads to observe a False-positive "held" flag from the reactor
    thread's acquisition, generating spurious advisories and (when fatal) daemon
    instability.

    The lock now has two layers:

    * the original process-local ``threading.Lock`` for in-process scheduler
      threads, and
    * the zeus-world LIVE writer flock for launchd sidecars in other processes.

    Without the flock, ``src.main`` and ``price_channel_daemon`` each held their
    own independent process-local mutex and still collided on SQLite's WAL writer
    lock, starving Day0/redecision/reactor claims under live load.
    """

    __slots__ = ("_lock", "_flock_fd")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flock_fd: int | None = None

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        acquired = self._lock.acquire(*args, **kwargs)
        if not acquired:
            return False
        try:
            blocking, timeout = _lock_acquire_kwargs(args, kwargs)
            fd = _acquire_flock_fd(
                _world_live_writer_lock_path(),
                blocking=blocking,
                timeout=timeout,
            )
            if fd is None:
                self._lock.release()
                return False
            self._flock_fd = fd
            _tls = _world_mutex_tls()
            _tls.held_depth = getattr(_tls, "held_depth", 0) + 1
            return True
        except BaseException:
            self._lock.release()
            raise

    def release(self) -> None:
        # Decrement the thread-local depth BEFORE releasing the OS lock so the
        # flag is already clear the instant another thread can re-acquire.
        _tls = _world_mutex_tls()
        d = getattr(_tls, "held_depth", 0)
        if d > 0:
            _tls.held_depth = d - 1
        fd = self._flock_fd
        self._flock_fd = None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    def __enter__(self) -> "bool":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


# Thread-local storage for the world mutex held depth.
# Each thread tracks its own depth independently — see _GuardedWorldMutex
# docstring for the rationale (thread-local vs process-global).
_WORLD_MUTEX_TLS = threading.local()


def _world_mutex_tls() -> threading.local:
    """Return the thread-local storage for the world mutex held depth."""
    return _WORLD_MUTEX_TLS


# Operations already warned about under the advisory (prod) posture of
# ``assert_no_world_mutex_held_for_io`` — warn once per operation, not per call,
# so a pre-existing site does not spam the log thousands of times per minute.
_WORLD_MUTEX_IO_ADVISED: set[str] = set()

_WORLD_DB_WRITE_MUTEX = _GuardedWorldMutex()


def world_mutex_is_held() -> bool:
    """True iff THIS THREAD currently holds the zeus-world.db write mutex.

    Returns True only when the calling thread has acquired the mutex and not
    yet released it. A background thread observing this function while a DIFFERENT
    thread holds the mutex correctly gets False — that thread's I/O is not
    a violation.

    Read by ``assert_no_world_mutex_held_for_io`` and by relationship tests that
    pin the "no I/O under the world write lock" invariant.
    """
    return getattr(_world_mutex_tls(), "held_depth", 0) > 0


def assert_no_world_mutex_held_for_io(operation: str) -> None:
    """Fail loudly if blocking I/O is attempted while the world mutex is held.

    Wire this at venue HTTP / on-chain RPC entrypoints (the two client surfaces
    that perform blocking network/chain reads). When the world write mutex is
    held, raising here turns a silent WAL-lock starvation wedge into an
    immediate, located ``WorldMutexIOViolation`` naming the offending I/O.

    ``operation`` is a short label for the I/O being attempted (e.g.
    ``"venue.get_trades"``, ``"onchain.eth_call"``); it appears in the error so
    the wedge's root call site is identified at the moment of the violation.

    DEPLOYMENT POSTURE (Phase 2 armed 2026-06-04): ALWAYS FATAL.
    The thread-local held-depth fix (same commit) eliminated all cross-thread
    false positives, so the only remaining violations are genuine same-thread
    I/O-under-mutex sites. With zero confirmed remaining sites this is safe to
    arm unconditionally — any new violation raises immediately, named, at the
    exact call site, preventing WAL-starvation regression permanently.

    To temporarily downgrade to advisory in an emergency (not recommended):
    set ``ZEUS_WORLD_MUTEX_IO_ADVISORY=1`` in the daemon environment.
    """
    if not world_mutex_is_held():
        return
    if os.environ.get("ZEUS_WORLD_MUTEX_IO_ADVISORY") == "1":
        if operation not in _WORLD_MUTEX_IO_ADVISED:
            _WORLD_MUTEX_IO_ADVISED.add(operation)
            logging.getLogger(__name__).warning(
                "WORLD_MUTEX_IO_ADVISORY: blocking I/O %r attempted under the "
                "world write mutex. Set ZEUS_WORLD_MUTEX_IO_ADVISORY=1 to "
                "suppress; remove to restore fatal enforcement.",
                operation,
            )
        return
    raise WorldMutexIOViolation(
        f"blocking I/O {operation!r} attempted while the zeus-world.db write "
        f"mutex is held — this serializes every world write behind the I/O "
        f"and wedges the daemon (WAL-lock starvation). The world write lock "
        f"MUST be released before any venue/on-chain call. See db.world_write_lock."
    )


def world_write_mutex() -> "_GuardedWorldMutex":
    """Return the process-global zeus-world.db write mutex.

    For callers that manage their own transaction lifecycle (e.g. the EDLI
    reactor's per-event SAVEPOINT + commit boundary) and need only the
    cross-thread exclusion, not the BEGIN IMMEDIATE/COMMIT wrapper of
    ``world_write_lock``. Hold it around the write txn only; never across HTTP.
    """
    return _WORLD_DB_WRITE_MUTEX


@contextlib.contextmanager
def world_write_lock(
    conn: sqlite3.Connection,
    *,
    immediate: bool = True,
):
    """Serialize a single zeus-world.db WRITE transaction across in-process threads.

    Acquire the process-global world-DB write mutex, open an explicit
    transaction (``BEGIN IMMEDIATE`` by default so the SQLite write lock is taken
    deterministically up front rather than lazily on the first DML), yield for
    the caller's writes, then ``COMMIT``. On any exception the transaction is
    ``ROLLBACK``-ed before the mutex is released, so a failing writer never holds
    either the mutex or the WAL write lock.

    Contract / scope:
      * Wrap ONLY the actual DB write txn. Never wrap a venue fetch / HTTP call /
        long compute inside this block — that would hold both the mutex and the
        WAL write lock across I/O and re-introduce starvation.
      * Reads do NOT need this lock (WAL permits concurrent readers). Only writers
        to zeus-world.db must go through it.
      * ``immediate=False`` opens a plain ``BEGIN`` (DEFERRED) when the caller has
        a reason to defer write-lock acquisition; the default ``True`` is correct
        for live writers and makes acquisition deterministic.

    Idempotent w.r.t. an already-open transaction: if ``conn`` is already inside a
    transaction (``conn.in_transaction``) we do NOT issue a nested BEGIN (SQLite
    forbids nested transactions); we still hold the mutex for the duration and
    COMMIT/ROLLBACK at the end, serializing the caller's write unit.
    """
    _WORLD_DB_WRITE_MUTEX.acquire()
    began = False
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            began = True
        yield conn
        # Commit the write unit. If we did not open the txn (caller already had
        # one open), still commit so the WAL write lock + mutex are released
        # promptly — the caller's write unit is the serialized boundary.
        conn.commit()
    except BaseException:
        # Release the WAL write lock immediately on failure so no other writer
        # waits out the busy_timeout behind a dead transaction.
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        _ = began  # retained for readability; rollback/commit cover both paths
        _WORLD_DB_WRITE_MUTEX.release()


def checkpoint_world_wal() -> tuple[int, int, int]:
    """Run ``PRAGMA wal_checkpoint(PASSIVE)`` on zeus-world.db; return its triple.

    THE BACKSTOP (2026-06-04 WAL checkpoint-starvation fix, part 2).

    Root (critic-proven, live): ``state/zeus-world.db-wal`` grows to GBs because
    long-lived READER connections hold a WAL snapshot (read-mark) across cycles.
    The checkpointer can only reclaim frames OLDER than the oldest active reader's
    mark, so while a reader pins the floor ``wal_checkpoint`` returns BUSY
    (``(1,-1,-1)``) and the -wal file never truncates → unbounded growth →
    eventual lock-starvation of opportunity_events emission (30-min ZERO
    candidates). Part 1 of the fix releases each long-lived reader's snapshot
    per cycle (``conn.rollback()`` between polls) so the floor advances; THIS
    function is the periodic backstop that actually reclaims the freed frames.

    Returns the ``(busy, log_frames, checkpointed_frames)`` triple from
    ``wal_checkpoint(PASSIVE)``. PASSIVE checkpoints copy all currently safe WAL
    frames without waiting for readers/writers or trying to truncate the file.
    That preserves live writer priority: a checkpoint must never sit in SQLite's
    busy handler while held-position monitor/redecision work is waiting to write.
    A chronic non-zero ``busy`` result is still observable in caller logs.

    Lock discipline: a checkpoint is NOT a write transaction. SQLite serializes
    checkpoints internally (the checkpoint lock), so this MUST NOT take the
    process-global world write mutex — holding it here would needlessly block
    every world writer for the checkpoint duration. A dedicated short-lived
    connection is used and closed immediately so it never itself becomes a
    floor-pinning reader.
    """
    conn = _connect(ZEUS_WORLD_DB_PATH, write_class=None)
    try:
        row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        # Row is (busy, log, checkpointed). Normalise to ints for callers/logs.
        busy = int(row[0]) if row is not None else 1
        log_frames = int(row[1]) if row is not None else -1
        ckpt_frames = int(row[2]) if row is not None else -1
        return (busy, log_frames, ckpt_frames)
    finally:
        conn.close()


def checkpoint_trades_wal() -> tuple[int, int, int]:
    """Run ``PRAGMA wal_checkpoint(PASSIVE)`` on zeus_trades.db; return its triple.

    THE BACKSTOP (2026-06-16) — the zeus_trades.db twin of ``checkpoint_world_wal``.

    Root (live-evidenced 2026-06-16): ``state/zeus_trades.db-wal`` grew to 810 MB
    because a long-lived READER connection in the live daemon held a WAL snapshot
    (read-mark) across cycles, pinning the WAL floor so ``wal_checkpoint`` returned
    BUSY and never truncated → unbounded growth → ``executable_market_snapshots``
    writes failed ``database is locked`` (auto-checkpoint contention on every write)
    → ``fresh_executable_city_count=0`` → the q-kernel spine could not price fresh
    families → no crosses. zeus-world.db already had this backstop; the trade DB did
    not. Same lock discipline: a dedicated short-lived connection, NO process-global
    write mutex (a checkpoint is not a write txn; SQLite serializes checkpoints
    internally), closed immediately so it never itself becomes a floor-pinning reader.

    Returns the ``(busy, log_frames, checkpointed_frames)`` triple. PASSIVE mode
    is intentional for live: reclaim safe frames without waiting behind active
    monitor writers or blocking the next held-position redecision tick.
    """
    conn = _connect(_zeus_trade_db_path(), write_class=None)
    try:
        row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        busy = int(row[0]) if row is not None else 1
        log_frames = int(row[1]) if row is not None else -1
        ckpt_frames = int(row[2]) if row is not None else -1
        return (busy, log_frames, ckpt_frames)
    finally:
        conn.close()


@contextlib.contextmanager
def get_forecasts_connection_with_world(
    *,
    write_class: WriteClass | str = "bulk",
):
    """Context manager: forecasts.db as MAIN with world.db ATTACHed.

    K1 P0 2026-05-14: ``daily_obs_append._write_atom_with_coverage`` writes
    BOTH ``observations`` (forecasts-class) AND ``data_coverage``
    (world-class) in a single SAVEPOINT.  A bare ``get_forecasts_connection``
    cannot service the ``data_coverage`` write; this helper opens
    forecasts.db as MAIN and ATTACHes world.db so both bare table names
    resolve correctly within the SAVEPOINT:

      - ``observations``   → MAIN (forecasts.db)  ✓
      - ``data_coverage``  → world (world.db via ATTACH)  ✓
      - ``daily_observation_revisions`` → world (world.db via ATTACH)  ✓

    Acquires writer-lock flocks on BOTH DBs in canonical alphabetical order
    (``zeus-forecasts.db`` before ``zeus-world.db``) to prevent deadlocks
    with other cross-DB writers (v4 §3.1.3 invariant).

    Callers MUST use this as a context manager and MUST NOT close the
    connection themselves — the ``finally`` block handles it.

    Authority: docs/archive/2026-Q2/task_2026-05-14_k1_followups/PLAN.md §2 P0
    CRITIC fix per IMPLEMENTATION_REVIEW_P0.md Pass D Option (a).
    """
    from src.state.db_writer_lock import (
        canonical_lock_order,
        db_writer_lock,
    )
    resolved = _resolve_write_class(write_class)
    if resolved is None:
        from src.state.db_writer_lock import WriteClass as _WC
        resolved = _WC.BULK
    # Canonical alphabetical sort: zeus-forecasts.db < zeus-world.db
    ordered_paths = canonical_lock_order(
        [ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH]
    )
    with db_writer_lock(ordered_paths[0], resolved):
        with db_writer_lock(ordered_paths[1], resolved):
            conn = _connect(ZEUS_FORECASTS_DB_PATH, write_class=resolved)
            try:
                attached = {
                    row[1]
                    for row in conn.execute("PRAGMA database_list").fetchall()
                }
                if "world" not in attached:
                    conn.execute(
                        "ATTACH DATABASE ? AS world",
                        (str(ZEUS_WORLD_DB_PATH),),
                    )
                yield conn
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass


@contextlib.contextmanager
def forecasts_connection_with_trades_flocked(
    *,
    write_class: WriteClass | str = "live",
):
    """Context manager: forecasts.db as MAIN with zeus_trades.db ATTACHed as 'trades'.

    INV-37 harvester fix (ChatGPT PR#408 review B1, 2026-06-14): the settlement
    harvester must write BOTH forecasts-class tables (settlements, calibration_pairs,
    ensemble_snapshots, observations) AND trade-class tables (position_current,
    position_events, decision_log, chronicle, settlement_commands) in a single
    attached-connection SAVEPOINT. Opening two independent connections and committing them
    separately violates INV-37 — a crash / busy / kill between the two commits
    leaves logically impossible state (settlement truth written but positions not
    settled, or the reverse).

    This helper opens forecasts.db as MAIN and ATTACHes zeus_trades.db as the
    'trades' schema, so:

      - forecasts-class bare names (settlements, calibration_pairs, observations,
        ensemble_snapshots) resolve to MAIN (forecasts.db) ✓
      - trade-class bare names (position_current, position_events, decision_log,
        chronicle, settlement_commands, executable_market_snapshots) are NOT present
        in forecasts.db so SQLite name resolution finds them in the attached
        'trades' schema (zeus_trades.db) ✓

    A single SAVEPOINT spanning all writes keeps normal successful execution
    all-or-nothing per INV-37 law. In WAL mode, ATTACHed DB files are not a
    cross-file host-crash-atomic contract; crash recovery must still prove or
    repair cross-file invariants.

    Acquires writer-lock flocks on BOTH DBs in canonical alphabetical order
    (``zeus-forecasts.db`` before ``zeus_trades.db``) to prevent deadlocks with
    other cross-DB writers (v4 §3.1.3 invariant).

    Authority: ChatGPT PR#408 review B1 INV-37,
    docs/evidence/pr408_review/chatgpt_deep_review_2026-06-14.md
    Created: 2026-06-14
    Last audited: 2026-06-14
    """
    from src.state.db_writer_lock import (
        canonical_lock_order,
        db_writer_lock,
    )
    resolved = _resolve_write_class(write_class)
    if resolved is None:
        from src.state.db_writer_lock import WriteClass as _WC
        resolved = _WC.LIVE
    # Canonical alphabetical sort: zeus-forecasts.db < zeus_trades.db
    ordered_paths = canonical_lock_order(
        [ZEUS_FORECASTS_DB_PATH, _zeus_trade_db_path()]
    )
    with db_writer_lock(ordered_paths[0], resolved):
        with db_writer_lock(ordered_paths[1], resolved):
            conn = _connect(ZEUS_FORECASTS_DB_PATH, write_class=resolved)
            try:
                attached = {
                    row[1]
                    for row in conn.execute("PRAGMA database_list").fetchall()
                }
                if "trades" not in attached:
                    conn.execute(
                        "ATTACH DATABASE ? AS trades",
                        (str(_zeus_trade_db_path()),),
                    )
                yield conn
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 — best-effort close
                    pass


def get_world_connection_with_trades_required(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """World connection (zeus-world.db MAIN) with zeus_trades.db ATTACHed as 'trades'.

    INV-37 price-channel fix (PR415 review B5, 2026-06-20) — the NON-flocked sibling
    of ``world_connection_with_trades_flocked``, for callers that must hold the
    connection across a LONG-LIVED loop (the forever market-channel ingestor thread)
    where holding cross-DB writer flocks for the whole lifetime would starve every
    other writer. Each write unit uses one attached SQLite connection and one
    ``commit()`` for normal successful-execution consistency, while the caller's
    world write mutex provides per-commit world-WAL serialization. WAL mode does
    not make MAIN + ATTACHed DB files a cross-file host-crash-atomic unit.

    world.db is MAIN so the EventStore's UNQUALIFIED ``opportunity_events`` (and its
    ``sqlite_master`` table-presence guard) resolve to the REAL world log; the
    feasibility write is schema-qualified ``trades.`` by the caller so it reaches the
    runtime-read trades table and never the world ghost copy (see
    ``world_connection_with_trades_flocked`` for the full ghost-table rationale).

    Fail closed: if ``trades`` cannot be ATTACHed, close and raise.

    Created: 2026-06-20
    Last audited: 2026-06-20
    Authority basis: PR415 ChatGPT deep-review B5 (INV-37), .claude/CLAUDE.md K1 DB split.
    """
    resolved = _resolve_write_class(write_class)
    conn = get_world_connection(write_class=resolved)
    try:
        attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
        if "trades" not in attached:
            conn.execute("ATTACH DATABASE ? AS trades", (str(_zeus_trade_db_path()),))
        return conn
    except Exception:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        raise


@contextlib.contextmanager
def world_connection_with_trades_flocked(
    *,
    write_class: WriteClass | str = "live",
):
    """Context manager: zeus-world.db as MAIN with zeus_trades.db ATTACHed as 'trades'.

    INV-37 price-channel fix (PR415 review B5, 2026-06-20): the held/candidate
    quote-evidence ingest must write BOTH the world event (``opportunity_events``,
    world-class, via EventWriter/EventStore) AND the trade-owned book witness
    (``execution_feasibility_evidence``, trade-class) in a single attached-connection
    SAVEPOINT.
    The prior shape opened two independent connections (``get_world_connection`` +
    ``get_trade_connection``) and committed them SEPARATELY — a crash/busy/kill
    between the two commits left divergent state, violating INV-37.

    GHOST-TABLE HAZARD (why this helper is world-MAIN, not trade-MAIN, and why the
    feasibility write must be schema-QUALIFIED): BOTH databases physically contain
    BOTH tables. ``world.opportunity_events`` is the real ~9.5M-row log while
    ``trades.opportunity_events`` is an empty ghost copy; conversely
    ``trades.execution_feasibility_evidence`` is the real ~4.3M-row table the live
    runtime reads (via the trade connection) while ``world.execution_feasibility_
    evidence`` is a populated-but-not-read legacy table (~12.9M rows). So UNQUALIFIED
    name resolution on a single ATTACHed connection is AMBIGUOUS — it resolves to the
    MAIN schema's copy, and ``EventStore._require_world_event_tables`` queries plain
    ``sqlite_master`` (MAIN-only), so a trade-MAIN+world-ATTACHed connection would
    silently write ``opportunity_events`` to the EMPTY trade ghost copy AND falsely pass
    the presence guard. The repo's existing flocked helpers
    (``forecasts_connection_with_trades_flocked``) only work because the non-MAIN
    table is ABSENT from MAIN — which is FALSE here. This helper therefore:

      - opens world.db as MAIN, so the EventStore's UNQUALIFIED ``opportunity_events``
        (and its ``sqlite_master`` guard) resolve to the REAL world log — NO
        EventStore change.
      - ATTACHes zeus_trades.db as ``trades`` so the feasibility write can target
        ``trades.execution_feasibility_evidence`` EXPLICITLY (the caller passes the
        ``trades`` qualifier), reaching the runtime-read table and NEVER the world
        ghost copy.

    A single SAVEPOINT / single commit spanning both writes keeps the pair
    all-or-nothing during normal successful execution per INV-37. In WAL mode,
    ATTACHed DB files are not a cross-file host-crash-atomic contract.

    Acquires writer-lock flocks on BOTH DBs in canonical alphabetical order
    (``zeus-world.db`` before ``zeus_trades.db``) — the SAME order as
    ``get_trade_connection_with_world_required`` /
    ``forecasts_connection_with_trades_flocked`` (both go through
    ``canonical_lock_order``), so there is no cross-writer lock-order inversion.

    Callers MUST use this as a context manager and MUST NOT close the connection
    themselves — the ``finally`` block handles it.

    Created: 2026-06-20
    Last audited: 2026-06-20
    Authority basis: PR415 ChatGPT deep-review B5 (INV-37), .claude/CLAUDE.md K1 DB split.
    """
    from src.state.db_writer_lock import (
        canonical_lock_order,
        db_writer_lock,
    )

    resolved = _resolve_write_class(write_class)
    if resolved is None:
        from src.state.db_writer_lock import WriteClass as _WC
        resolved = _WC.LIVE
    # Canonical alphabetical sort: zeus-world.db < zeus_trades.db
    ordered_paths = canonical_lock_order([ZEUS_WORLD_DB_PATH, _zeus_trade_db_path()])
    with db_writer_lock(ordered_paths[0], resolved):
        with db_writer_lock(ordered_paths[1], resolved):
            conn = _connect(ZEUS_WORLD_DB_PATH, write_class=resolved)
            try:
                attached = {
                    row[1]
                    for row in conn.execute("PRAGMA database_list").fetchall()
                }
                if "trades" not in attached:
                    conn.execute(
                        "ATTACH DATABASE ? AS trades",
                        (str(_zeus_trade_db_path()),),
                    )
                yield conn
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 — best-effort close
                    pass


def get_backtest_connection(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Derived backtest DB connection.

    This DB is a reporting/audit surface only. Live runtime execution must not
    read it as authority or write trade/world truth through it.
    """
    return _connect(ZEUS_BACKTEST_DB_PATH, write_class=write_class)


def get_trade_connection_with_world_optional(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Trade connection with shared DB ATTACH attempted for optional joins.

    v4 plan §3.1.3: when an explicit ``write_class`` is supplied, the
    helper records ATTACH order under the canonical alphabetical sort
    (``risk_state.db < zeus-world.db < zeus_trades.db``) so concurrent
    cross-DB writers cannot deadlock. Without an explicit class, behavior
    matches pre-v4 (single ATTACH; no flocks).

    For *flock-acquired* cross-DB writes use the
    :func:`trade_connection_with_world_flocked` context manager instead;
    that surface acquires the per-DB writer locks in canonical order before
    yielding the ATTACHed connection.
    """
    from src.state.db_writer_lock import canonical_lock_order
    resolved = _resolve_write_class(write_class)
    conn = get_trade_connection(write_class=resolved)
    # Guard: skip ATTACH if 'world' schema already present (connection reuse)
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" not in attached:
        # Canonical order is recorded for telemetry; ATTACH targets are
        # the same single 'world' schema in this surface but the helper
        # call exercises the v4 §3.1.3 ordering invariant.
        if resolved is not None:
            _ = canonical_lock_order([_zeus_trade_db_path(), ZEUS_WORLD_DB_PATH])
            _cnt_inc("db_trade_with_world_canonical_order_total")
        try:
            conn.execute("ATTACH DATABASE ? AS world", (str(ZEUS_WORLD_DB_PATH),))
        except sqlite3.OperationalError as exc:
            logger.warning("ATTACH world failed (non-fatal): %r", exc)
    # K1 (2026-05-11): also ATTACH forecasts DB so cross-DB joins on
    # ensemble_snapshots / settlements / settlement_outcomes / market_events
    # remain possible from trade-conn query paths (evaluator, replay).
    if "forecasts" not in attached:
        try:
            conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
        except sqlite3.OperationalError as exc:
            logger.warning("ATTACH forecasts failed (non-fatal): %r", exc)
    return conn


def get_trade_connection_with_world_required(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Trade connection with world/forecasts ATTACHed or fail closed.

    Live money authority paths use this flavor because a returned connection
    without ``world`` or ``forecasts`` can silently route around canonical
    market/order/position truth.  Diagnostics and read-only compatibility paths
    may still use ``get_trade_connection_with_world_optional``.
    """
    from src.state.db_writer_lock import canonical_lock_order

    resolved = _resolve_write_class(write_class)
    conn = get_trade_connection(write_class=resolved)
    try:
        attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
        if "world" not in attached:
            if resolved is not None:
                _ = canonical_lock_order([_zeus_trade_db_path(), ZEUS_WORLD_DB_PATH])
                _cnt_inc("db_trade_with_world_canonical_order_total")
            conn.execute("ATTACH DATABASE ? AS world", (str(ZEUS_WORLD_DB_PATH),))
            attached.add("world")
        if "forecasts" not in attached:
            conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
            attached.add("forecasts")
        return conn
    except Exception:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        raise


def get_trade_connection_with_world(
    *, write_class: WriteClass | str | None = None,
) -> sqlite3.Connection:
    """Backward-compatible optional ATTACH helper.

    New live-money authority paths should call
    :func:`get_trade_connection_with_world_required`.
    """
    return get_trade_connection_with_world_optional(write_class=write_class)


@contextlib.contextmanager
def trade_connection_with_world_flocked(
    *,
    write_class: WriteClass | str = "live",
):
    """Context manager: cross-DB write with canonical-order flocks.

    v4 plan §3.1.3 (deadlock-free cross-DB writers). Acquires the per-DB
    writer locks for ``zeus_trades.db`` and ``zeus-world.db`` in canonical
    alphabetical order, ATTACHes ``world`` onto a trade connection, yields
    that connection, and releases the flocks (and connection) on exit.

    Default ``write_class="live"`` matches the dominant call-site shape
    (riskguard + harvester + settlement commands). Phase 1+ callers
    retrofit by replacing ``conn = get_trade_connection_with_world()``
    blocks with ``with trade_connection_with_world_flocked(...) as conn:``.
    """
    from src.state.db_writer_lock import (
        canonical_lock_order,
        db_writer_lock,
    )
    resolved = _resolve_write_class(write_class)
    if resolved is None:
        # write_class explicit & non-None — should always resolve.
        from src.state.db_writer_lock import WriteClass as _WC
        resolved = _WC.LIVE
    ordered_paths = canonical_lock_order(
        [_zeus_trade_db_path(), ZEUS_WORLD_DB_PATH]
    )
    # Stack two flock context managers (canonical order) before opening conn.
    with db_writer_lock(ordered_paths[0], resolved):
        with db_writer_lock(ordered_paths[1], resolved):
            conn = get_trade_connection(write_class=resolved)
            try:
                attached = {
                    row[1]
                    for row in conn.execute("PRAGMA database_list").fetchall()
                }
                if "world" not in attached:
                    conn.execute(
                        "ATTACH DATABASE ? AS world",
                        (str(ZEUS_WORLD_DB_PATH),),
                    )
                _cnt_inc(
                    f"db_trade_with_world_flocked_{resolved.value}_total"
                )
                yield conn
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 — best-effort close
                    pass


logger = logging.getLogger(__name__)


def _handle_db_write_lock(exc: sqlite3.OperationalError) -> None:
    """T1E: log degrade counter + ALERT when 'database is locked' is raised.

    Called by connection helpers when sqlite3 busy-timeout expires and the
    live cycle must continue in read-only monitor mode rather than crashing.
    Does NOT re-raise — caller decides whether to return None or raise.
    """
    _cnt_inc("db_write_lock_timeout_total")
    logger.warning(
        "telemetry_counter event=db_write_lock_timeout_total db_error=%r",
        str(exc),
    )
    logger.error(
        "ALERT db_write_lock_timeout: database is locked after busy-timeout; "
        "cycle degrades to read-only monitor for this cycle. error=%r",
        str(exc),
    )


def connect_or_degrade(
    db_path: Path,
    *,
    write_class: WriteClass | str | None = None,
) -> Optional[sqlite3.Connection]:
    """T1E: Connect to DB; on 'database is locked' degrade to None (read-only cycle).

    Used by the live cycle write path. Returns None when the DB is locked so
    the caller can skip writes for this cycle without crashing the daemon.
    Any other OperationalError is re-raised (not a lock timeout).
    """
    try:
        return _connect(db_path, write_class=write_class)
    except sqlite3.OperationalError as exc:
        if str(exc).startswith("database is locked"):
            _handle_db_write_lock(exc)
            return None
        raise


CANONICAL_POSITION_SETTLED_CONTRACT_VERSION = "position_settled.v1"
LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE = "legacy_lifecycle_projection_not_settlement_authority"
SETTLEMENT_AUTHORITY_DIAGNOSTIC_SOURCE = "position_events_or_decision_log_verified_settlement"
EXECUTION_FACT_AUTHORITY_SCOPE = "execution_lifecycle_projection_not_settlement_authority"
CANONICAL_POSITION_SETTLED_DETAIL_FIELDS = (
    "contract_version",
    "winning_bin",
    "position_bin",
    "won",
    "outcome",
    "p_posterior",
    "exit_price",
    "pnl",
    "exit_reason",
    "settlement_authority",
    "settlement_truth_source",
    "settlement_market_slug",
    "settlement_temperature_metric",
    "settlement_source",
    "settlement_value",
)
SETTLEMENT_METRIC_READY_TRUTH_SOURCES = frozenset({
    "forecasts.settlement_outcomes",
    "forecasts.settlements",
    "world.settlements",
    "harvester_live_verified_settlement",
})
AUTHORITATIVE_SETTLEMENT_ROW_REQUIRED_FIELDS = (
    "trade_id",
    "city",
    "target_date",
    "range_label",
    "direction",
    "p_posterior",
    "outcome",
    "pnl",
    "settled_at",
)
OPEN_EXPOSURE_PHASES = (
    "pending_entry",
    "active",
    "day0_window",
    "pending_exit",
    "unknown",
)
ENTRY_ECONOMICS_LEGACY_UNKNOWN = "legacy_unknown"
ENTRY_ECONOMICS_AVG_FILL_PRICE = "avg_fill_price"
ENTRY_ECONOMICS_CORRECTED_COST_BASIS = "corrected_executable_cost_basis"
FILL_AUTHORITY_NONE = "none"
FILL_AUTHORITY_VENUE_POSITION_OBSERVED = "venue_position_observed"
FILL_AUTHORITY_VENUE_CONFIRMED_FULL = "venue_confirmed_full"
TERMINAL_TRADE_DECISION_STATUSES = frozenset(
    {
        "exited",
        "settled",
        "voided",
        "admin_closed",
        "unresolved_ghost",
    }
)
PORTFOLIO_LOADER_PHASE_TO_RUNTIME_STATE = {
    "pending_entry": "pending_tracked",
    "active": "entered",
    "day0_window": "day0_window",
    "pending_exit": "pending_exit",
    "economically_closed": "economically_closed",
    "settled": "settled",
    "voided": "voided",
    "quarantined": "quarantined",
    "admin_closed": "admin_closed",
}


def _portfolio_loader_runtime_state_from_phase(phase: object) -> str:
    phase_value = str(phase or "")
    return PORTFOLIO_LOADER_PHASE_TO_RUNTIME_STATE.get(phase_value, phase_value)


def _positive_finite_decimal_text(value: object) -> int:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return int(parsed.is_finite() and parsed > 0)


def _decimal_text_equal(left: object, right: object) -> int:
    try:
        left_parsed = Decimal(str(left))
        right_parsed = Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return int(
        left_parsed.is_finite()
        and right_parsed.is_finite()
        and left_parsed == right_parsed
    )


def _install_connection_functions(conn: sqlite3.Connection) -> None:
    functions = (
        ("zeus_positive_decimal_text", 1, _positive_finite_decimal_text),
        ("zeus_decimal_text_equal", 2, _decimal_text_equal),
    )
    for name, arity, func in functions:
        try:
            conn.create_function(name, arity, func, deterministic=True)
        except TypeError:
            conn.create_function(name, arity, func)


def init_provenance_projection_schema(conn: sqlite3.Connection) -> None:
    """Create U2 raw-provenance projection tables and legacy migrations.

    U2 is intentionally append-only: command/order/trade/lot facts are facts,
    not mutable current-state rows. Later phases may derive read models from
    these tables, but they must not mutate historical provenance.
    """

    _install_connection_functions(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS venue_submission_envelopes (
          envelope_id TEXT PRIMARY KEY,
          schema_version INTEGER NOT NULL DEFAULT 1,
          sdk_package TEXT NOT NULL,
          sdk_version TEXT NOT NULL,
          host TEXT NOT NULL,
          chain_id INTEGER NOT NULL,
          funder_address TEXT NOT NULL,
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL,
          yes_token_id TEXT NOT NULL,
          no_token_id TEXT NOT NULL,
          selected_outcome_token_id TEXT NOT NULL,
          outcome_label TEXT NOT NULL CHECK (outcome_label IN ('YES','NO')),
          side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
          price TEXT NOT NULL,
          size TEXT NOT NULL,
          order_type TEXT NOT NULL CHECK (order_type IN ('GTC','GTD','FOK','FAK')),
          post_only INTEGER NOT NULL CHECK (post_only IN (0,1)),
          tick_size TEXT NOT NULL,
          min_order_size TEXT NOT NULL,
          neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
          fee_details_json TEXT NOT NULL,
          canonical_pre_sign_payload_hash TEXT NOT NULL,
          signed_order_blob BLOB,
          signed_order_hash TEXT,
          raw_request_hash TEXT NOT NULL,
          raw_response_json TEXT,
          order_id TEXT,
          trade_ids_json TEXT NOT NULL DEFAULT '[]',
          transaction_hashes_json TEXT NOT NULL DEFAULT '[]',
          error_code TEXT,
          error_message TEXT,
          captured_at TEXT NOT NULL
        );

        CREATE TRIGGER IF NOT EXISTS venue_submission_envelopes_no_update
        BEFORE UPDATE ON venue_submission_envelopes
        BEGIN
          SELECT RAISE(ABORT, 'venue_submission_envelopes is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS venue_submission_envelopes_no_delete
        BEFORE DELETE ON venue_submission_envelopes
        BEGIN
          SELECT RAISE(ABORT, 'venue_submission_envelopes is append-only');
        END;

        CREATE TABLE IF NOT EXISTS venue_order_facts (
          fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
          venue_order_id TEXT NOT NULL,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id),
          state TEXT NOT NULL CHECK (state IN (
            'LIVE','RESTING','MATCHED','PARTIALLY_MATCHED',
            'CANCEL_REQUESTED','CANCEL_CONFIRMED','CANCEL_UNKNOWN','CANCEL_FAILED',
            'EXPIRED','VENUE_WIPED','HEARTBEAT_CANCEL_SUSPECTED'
          )),
          remaining_size TEXT,
          matched_size TEXT,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT,
          UNIQUE (venue_order_id, local_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_order_facts_command ON venue_order_facts (command_id, observed_at);
        CREATE INDEX IF NOT EXISTS idx_order_facts_state ON venue_order_facts (state, observed_at);

        CREATE TRIGGER IF NOT EXISTS venue_order_facts_no_update
        BEFORE UPDATE ON venue_order_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_order_facts is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS venue_order_facts_no_delete
        BEFORE DELETE ON venue_order_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_order_facts is append-only');
        END;

        CREATE TABLE IF NOT EXISTS venue_trade_facts (
          trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
          trade_id TEXT NOT NULL,
          venue_order_id TEXT NOT NULL,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id),
          state TEXT NOT NULL CHECK (state IN ('MATCHED','MINED','CONFIRMED','RETRYING','FAILED')),
          filled_size TEXT NOT NULL,
          fill_price TEXT NOT NULL,
          fee_paid_micro INTEGER,
          tx_hash TEXT,
          block_number INTEGER,
          confirmation_count INTEGER DEFAULT 0,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT,
          UNIQUE (trade_id, local_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_trade_facts_command ON venue_trade_facts (command_id, observed_at);
        CREATE INDEX IF NOT EXISTS idx_trade_facts_trade ON venue_trade_facts (trade_id, observed_at);

        CREATE TRIGGER IF NOT EXISTS venue_trade_facts_no_update
        BEFORE UPDATE ON venue_trade_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_trade_facts is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS venue_trade_facts_no_delete
        BEFORE DELETE ON venue_trade_facts
        BEGIN
          SELECT RAISE(ABORT, 'venue_trade_facts is append-only');
        END;

        CREATE TABLE IF NOT EXISTS position_lots (
          lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
          position_id INTEGER NOT NULL,
          state TEXT NOT NULL CHECK (state IN (
            'OPTIMISTIC_EXPOSURE','CONFIRMED_EXPOSURE',
            'EXIT_PENDING','ECONOMICALLY_CLOSED_OPTIMISTIC',
            'ECONOMICALLY_CLOSED_CONFIRMED','SETTLED','QUARANTINED'
          )),
          shares TEXT NOT NULL,
          entry_price_avg TEXT NOT NULL,
          exit_price_avg TEXT,
          source_command_id TEXT REFERENCES venue_commands(command_id),
          source_trade_fact_id INTEGER REFERENCES venue_trade_facts(trade_fact_id),
          captured_at TEXT NOT NULL,
          state_changed_at TEXT NOT NULL,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT,
          UNIQUE (position_id, local_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_position_lots_state ON position_lots (state, position_id);
        CREATE INDEX IF NOT EXISTS idx_position_lots_trade ON position_lots (source_trade_fact_id);

        CREATE TRIGGER IF NOT EXISTS position_lots_optimistic_trade_authority
        BEFORE INSERT ON position_lots
        WHEN NEW.state = 'OPTIMISTIC_EXPOSURE'
        BEGIN
          SELECT RAISE(ABORT, 'OPTIMISTIC_EXPOSURE requires MATCHED/MINED source trade fact authority')
          WHERE NOT EXISTS (
            SELECT 1
              FROM venue_trade_facts tf
              JOIN venue_commands cmd
                ON cmd.command_id = tf.command_id
             WHERE tf.trade_fact_id = NEW.source_trade_fact_id
               AND tf.command_id = NEW.source_command_id
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
               AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
               AND tf.state IN ('MATCHED','MINED')
               AND zeus_positive_decimal_text(tf.filled_size) = 1
               AND zeus_positive_decimal_text(tf.fill_price) = 1
               AND zeus_decimal_text_equal(tf.filled_size, NEW.shares) = 1
               AND zeus_decimal_text_equal(tf.fill_price, NEW.entry_price_avg) = 1
          );
        END;

        CREATE TRIGGER IF NOT EXISTS position_lots_confirmed_trade_authority
        BEFORE INSERT ON position_lots
        WHEN NEW.state = 'CONFIRMED_EXPOSURE'
        BEGIN
          SELECT RAISE(ABORT, 'CONFIRMED_EXPOSURE requires CONFIRMED source trade fact authority')
          WHERE NOT EXISTS (
            SELECT 1
              FROM venue_trade_facts tf
              JOIN venue_commands cmd
                ON cmd.command_id = tf.command_id
             WHERE tf.trade_fact_id = NEW.source_trade_fact_id
               AND tf.command_id = NEW.source_command_id
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
               AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
               AND tf.state = 'CONFIRMED'
               AND zeus_positive_decimal_text(tf.filled_size) = 1
               AND zeus_positive_decimal_text(tf.fill_price) = 1
               AND zeus_decimal_text_equal(tf.filled_size, NEW.shares) = 1
               AND zeus_decimal_text_equal(tf.fill_price, NEW.entry_price_avg) = 1
          );
        END;

        CREATE TRIGGER IF NOT EXISTS position_lots_no_update
        BEFORE UPDATE ON position_lots
        BEGIN
          SELECT RAISE(ABORT, 'position_lots is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS position_lots_no_delete
        BEFORE DELETE ON position_lots
        BEGIN
          SELECT RAISE(ABORT, 'position_lots is append-only');
        END;

        CREATE TABLE IF NOT EXISTS provenance_envelope_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          subject_type TEXT NOT NULL CHECK (subject_type IN ('command','order','trade','lot','settlement','wrap_unwrap','heartbeat')),
          subject_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          payload_json TEXT,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
          local_sequence INTEGER NOT NULL,
          UNIQUE (subject_type, subject_id, local_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_envelope_events_subject ON provenance_envelope_events (subject_type, subject_id, observed_at);

        CREATE TRIGGER IF NOT EXISTS provenance_envelope_events_no_update
        BEFORE UPDATE ON provenance_envelope_events
        BEGIN
          SELECT RAISE(ABORT, 'provenance_envelope_events is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS provenance_envelope_events_no_delete
        BEFORE DELETE ON provenance_envelope_events
        BEGIN
          SELECT RAISE(ABORT, 'provenance_envelope_events is append-only');
        END;
        """
    )

    try:
        conn.execute("ALTER TABLE venue_commands ADD COLUMN envelope_id TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_venue_commands_envelope ON venue_commands(envelope_id);")


DEFAULT_CONTROL_OVERRIDE_PRECEDENCE = 100
TOKEN_SUPPRESSION_REASONS = frozenset({
    "operator_quarantine_clear",
    "chain_only_quarantined",
    "settled_position",
})
RESOLVED_TOKEN_SUPPRESSION_REASONS = (
    "operator_quarantine_clear",
    "settled_position",
)


def get_connection(
    db_path: Optional[Path] = None,
    *,
    write_class: WriteClass | str | None = "bulk",
) -> sqlite3.Connection:
    """Legacy connection helper.

    v4 plan §AX3: default ``write_class="bulk"`` because the surviving
    callers of this surface are dominated by backfill / replay / etl /
    audit scripts and the legacy ``zeus.db`` path, all of which are BULK
    by classification. LIVE call sites must opt in explicitly with
    ``write_class="live"`` so the v4 flock topology routes them through
    the LIVE flock once Phase 1 retrofits land. Pass ``write_class=None``
    to suppress classification entirely (pre-v4 behavior).
    """
    db_path = db_path or ZEUS_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # T1E: timeout read from ZEUS_DB_BUSY_TIMEOUT_MS env var (ms→s); default 30s.
    timeout_s = _db_busy_timeout_s()
    conn = sqlite3.connect(str(db_path), timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # 2026-05-12 antibody (cold-cache K3 partial fix): bump page cache to 1 GB
    # so the hot working set of large forecast tables stays resident across
    # cycles. Default is ~2 MB which is fatal for 35 GB forecasts.db cold-cache
    # B-tree descent. ZEUS_DB_CACHE_KB env var overrides; -1048576 = 1 GiB.
    # Per-connection, so independent for trade/world/forecasts/backtest.
    cache_kb = int(os.environ.get("ZEUS_DB_CACHE_KB", "1048576"))
    conn.execute(f"PRAGMA cache_size = -{cache_kb}")
    # 2026-05-13 antibody (cold-cache K3 follow-up): mirror _connect()'s
    # mmap_size setting on this alternate connection helper. See _connect()
    # for rationale. Without this, callers using get_connection() would
    # still thrash the bounded SQLite cache on the 51 GB forecasts.db.
    mmap_bytes = int(os.environ.get("ZEUS_DB_MMAP_BYTES", str(32 * 1024 * 1024 * 1024)))
    conn.execute(f"PRAGMA mmap_size = {mmap_bytes}")
    _install_connection_functions(conn)
    # CATEGORY ANTIBODY (Fitz #5): same SQL-level wait budget as _connect(). Every
    # get_connection() handle (riskguard reads/writes, schema-ensure callers) now
    # carries the busy_timeout so transient WAL contention WAITS instead of raising
    # "database is locked". Connection PRAGMA only — INV-37 / txn semantics intact.
    _apply_busy_timeout(conn)
    resolved = _resolve_write_class(write_class)
    if resolved is not None:
        _cnt_inc(f"db_get_connection_{resolved.value}_total")
    return conn


# Last reused or audited: 2026-05-11
# Authority basis: PLAN docs/operations/task_2026-05-11_init_schema_boot_invariant/PLAN.md
# Schema currency sentinel. Bump on EVERY DDL change in this file OR in
# init_provenance_projection_schema (:1729) OR apply_v2_schema (:2222).
# CI hook scripts/check_schema_version.py diffs the sqlite_master hash of
# a fresh-init DB against tests/state/_schema_pinned_hash.txt and fails
# the PR if SCHEMA_VERSION did not change in lockstep.
SCHEMA_VERSION = 42  # 2026-05-28 F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1): position_current gains chain_avg_price + chain_cost_basis_usd so balance-only rescue persists chain-observed economics without overwriting submitted entry_price/cost_basis_usd/size_usd. Prior: 41 = merge #349+#352.


def init_schema(
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Create world-class Zeus tables. Idempotent.

    Post-K1 split (2026-05-11): init_schema creates world-class tables plus
    legacy_archived ghost copies of forecast-class tables (observations,
    settlements, source_run, *_v2) declared schema_class=legacy_archived in
    architecture/db_table_ownership.yaml. The ghost copies are safe to drop
    after the D2 90-day retain window (2026-08-09) via drop_world_ghost_tables.py.
    New forecast-class tables are created by init_schema_forecasts on zeus-forecasts.db.
    The _v2_forecast_tables kwarg is RETIRED in P2; apply_canonical_schema is always
    called with forecast_tables=False here (P2 DDL refactor, 2026-05-14).

    # Fix (task #200, 2026-05-10): PRAGMA busy_timeout must be re-applied at the
    # start of init_schema. Python's sqlite3.connect(timeout=N) installs a C-level
    # busy handler, but sqlite3.executescript() resets that handler to NULL before
    # running its SQL. Every executescript() call in this function (there are ~6)
    # wipes the timeout, leaving subsequent conn.execute() calls with no wait budget.
    # Re-applying PRAGMA busy_timeout here covers the entire init_schema call including
    # apply_canonical_schema. Source: ZEUS_DB_BUSY_TIMEOUT_MS env var (ms), default 30 s.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    # Re-apply busy_timeout: executescript() resets the C-level busy handler.
    _busy_ms = int(os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))
    conn.execute(f"PRAGMA busy_timeout = {_busy_ms}")

    conn.executescript("""
        -- Migration-created world-class tables promoted from legacy_archived (2026-07-01, atlas §6C):
        -- declared world_class in db_table_ownership.yaml + hold live data; created here so init == registry.
        CREATE TABLE IF NOT EXISTS historical_forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            forecast_high REAL NOT NULL,
            temp_unit TEXT NOT NULL,
            lead_days INTEGER,
            available_at TEXT,
            UNIQUE(city, target_date, source, lead_days)
        );
        CREATE TABLE IF NOT EXISTS hko_hourly_accumulator (
            target_date TEXT NOT NULL,
            hour_utc    TEXT NOT NULL,
            temperature REAL NOT NULL,
            fetched_at  TEXT NOT NULL,
            PRIMARY KEY (target_date, hour_utc)
        );
        CREATE TABLE IF NOT EXISTS day0_oracle_anomaly_flags (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            flagged_at TEXT NOT NULL,
            ttl_hours REAL NOT NULL,
            detail TEXT NOT NULL,
            PRIMARY KEY (city, target_date)
        );
        -- Inherited from legacy predecessor: settlement outcomes (world-class authoritative table)
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            market_slug TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_source TEXT,
            settled_at TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            pm_bin_lo REAL,
            pm_bin_hi REAL,
            unit TEXT,
            settlement_source_type TEXT,
            -- REOPEN-2 inline: INV-14 identity spine is part of the fresh-DB
            -- schema so UNIQUE(city, target_date, temperature_metric) can
            -- reference temperature_metric without a second migration pass.
            -- Legacy DBs that predate these columns get them via the ALTER
            -- loop below, and their UNIQUE constraint is upgraded via the
            -- REOPEN-2 table-rebuild migration that runs between the ALTERs
            -- and the trigger reinstall.
            temperature_metric TEXT
                CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT
                CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
            data_version TEXT,
            provenance_json TEXT,
            UNIQUE(city, target_date, temperature_metric)
        );

        -- Inherited: IEM ASOS, NOAA GHCND, Meteostat, WU PWS
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            high_temp REAL,
            low_temp REAL,
            unit TEXT NOT NULL,
            station_id TEXT,
            fetched_at TEXT,
            -- K1 additions: raw value/unit contract
            high_raw_value REAL,
            high_raw_unit TEXT CHECK (high_raw_unit IN ('F', 'C', 'K')),
            high_target_unit TEXT CHECK (high_target_unit IN ('F', 'C')),
            low_raw_value REAL,
            low_raw_unit TEXT CHECK (low_raw_unit IN ('F', 'C', 'K')),
            low_target_unit TEXT CHECK (low_target_unit IN ('F', 'C')),
            -- K1 additions: temporal provenance
            high_fetch_utc TEXT,
            high_local_time TEXT,
            high_collection_window_start_utc TEXT,
            high_collection_window_end_utc TEXT,
            low_fetch_utc TEXT,
            low_local_time TEXT,
            low_collection_window_start_utc TEXT,
            low_collection_window_end_utc TEXT,
            -- K1 additions: DST context
            timezone TEXT,
            utc_offset_minutes INTEGER,
            dst_active INTEGER CHECK (dst_active IN (0, 1)),
            is_ambiguous_local_hour INTEGER CHECK (is_ambiguous_local_hour IN (0, 1)),
            is_missing_local_hour INTEGER CHECK (is_missing_local_hour IN (0, 1)),
            -- K1 additions: geographic/seasonal
            hemisphere TEXT CHECK (hemisphere IN ('N', 'S')),
            season TEXT CHECK (season IN ('DJF', 'MAM', 'JJA', 'SON')),
            month INTEGER CHECK (month BETWEEN 1 AND 12),
            -- K1 additions: run provenance
            rebuild_run_id TEXT,
            data_source_version TEXT,
            -- K1 additions: authority + extensibility
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            high_provenance_metadata TEXT,  -- JSON
            low_provenance_metadata TEXT,  -- JSON
            UNIQUE(city, target_date, source)
        );

        CREATE TABLE IF NOT EXISTS daily_observation_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            natural_key_json TEXT NOT NULL DEFAULT '{}',
            existing_row_id INTEGER NOT NULL,
            existing_combined_payload_hash TEXT,
            incoming_combined_payload_hash TEXT NOT NULL,
            existing_high_payload_hash TEXT,
            existing_low_payload_hash TEXT,
            incoming_high_payload_hash TEXT NOT NULL,
            incoming_low_payload_hash TEXT NOT NULL,
            reason TEXT NOT NULL CHECK (
                reason IN ('payload_hash_mismatch', 'missing_existing_payload_hash')
            ),
            writer TEXT NOT NULL,
            existing_row_json TEXT NOT NULL,
            incoming_row_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
        );

        -- market_events DDL removed in B3cont (PR3): dead v1 shell (0 rows).
        -- Canonical table is market_events on zeus-forecasts.db (collapsed from market_events).
        -- Live DB migration: pr3_b3_live_table_rename.py (operator-run, not committed).

        -- Inherited: historical prices for baseline backtesting
        -- city/target_date/range_label carried over from legacy predecessor for bin mapping
        CREATE TABLE IF NOT EXISTS token_price_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            price REAL NOT NULL,
            volume REAL,
            bid REAL,
            ask REAL,
            spread REAL,
            source_timestamp TEXT,
            timestamp TEXT NOT NULL
        );

        -- v1.F20 (2026-05-18): ensemble_snapshots (legacy world-class) removed.
        -- Canonical table is ensemble_snapshots in zeus-forecasts.db (K1 split).
        -- DROP migration: scripts/migrations/202605_drop_ensemble_snapshots_legacy.py
        -- DDL removed here to prevent recreating the table on every boot after
        -- the operator runs the DROP migration.

        -- calibration_pairs bare shell dropped (B3 rename: table now owned by
        -- apply_canonical_schema/_create_calibration_pairs with canonical v2 schema).

        -- Independent forecast-event units derived from calibration_pairs.
        -- Behavior-neutral substrate: active Platt routing still uses existing
        -- pair APIs until a later cutover packet explicitly switches maturity.
        CREATE TABLE IF NOT EXISTS calibration_decision_group (
            group_id TEXT PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            forecast_available_at TEXT NOT NULL,
            cluster TEXT NOT NULL,
            season TEXT NOT NULL,
            lead_days REAL NOT NULL,
            settlement_value REAL,
            winning_range_label TEXT,
            bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1)),
            n_pair_rows INTEGER NOT NULL,
            n_positive_rows INTEGER NOT NULL,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_calibration_decision_group_bucket
            ON calibration_decision_group(cluster, season, lead_days);

        -- B3cont: bare platt_models DDL removed (0 rows; canonical table is platt_models in zeus-forecasts.db via v2_schema.py)

        -- Trade decisions with full audit trail
        CREATE TABLE IF NOT EXISTS trade_decisions (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            bin_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            size_usd REAL NOT NULL,
            price REAL NOT NULL,
            timestamp TEXT NOT NULL,
            forecast_snapshot_id INTEGER,  -- v1.F20: soft ref to ensemble_snapshots.snapshot_id (cross-DB, no FK constraint)
            calibration_model_version TEXT,
            p_raw REAL NOT NULL,
            p_calibrated REAL,
            p_posterior REAL NOT NULL,
            edge REAL NOT NULL,
            ci_lower REAL NOT NULL,
            ci_upper REAL NOT NULL,
            kelly_fraction REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            filled_at TEXT,
            fill_price REAL,
            runtime_trade_id TEXT,
            order_id TEXT,
            order_status_text TEXT,
            order_posted_at TEXT,
            entered_at_ts TEXT,
            chain_state TEXT,
            -- Attribution fields (CLAUDE.md: mandatory on every trade)
            strategy TEXT,
            edge_source TEXT,
            bin_type TEXT,
            discovery_mode TEXT,
            market_hours_open REAL,
            fill_quality REAL,
            entry_method TEXT,
            selected_method TEXT,
            applied_validations_json TEXT,
            exit_trigger TEXT,
            exit_reason TEXT,
            admin_exit_reason TEXT,
            exit_divergence_score REAL DEFAULT 0.0,
            exit_market_velocity_1h REAL DEFAULT 0.0,
            exit_forward_edge REAL DEFAULT 0.0,
            -- Phase 2 Domain Object Snapshots (JSON flattened blobs)
            settlement_semantics_json TEXT,
            epistemic_context_json TEXT,
            edge_context_json TEXT,
            -- Phase 3: Shadow Proof True Attribution
            entry_alpha_usd REAL DEFAULT 0.0,
            execution_slippage_usd REAL DEFAULT 0.0,
            exit_timing_usd REAL DEFAULT 0.0,
            risk_throttling_usd REAL DEFAULT 0.0,
            settlement_edge_usd REAL DEFAULT 0.0
        );

        -- Durable per-decision probability lineage.
        -- This is not portfolio/lifecycle authority; it records decision-time
        -- probability vectors and explicit completeness status for replay/audit.
        CREATE TABLE IF NOT EXISTS probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            decision_snapshot_id TEXT,
            candidate_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            range_label TEXT,
            direction TEXT CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
            mode TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            entry_method TEXT,
            selected_method TEXT,
            trace_status TEXT NOT NULL CHECK (trace_status IN (
                'complete',
                'degraded_decision_context',
                'degraded_missing_vectors',
                'pre_vector_unavailable'
            )),
            missing_reason_json TEXT NOT NULL DEFAULT '[]',
            bin_labels_json TEXT,
            p_raw_json TEXT,
            p_cal_json TEXT,
            p_market_json TEXT,
            p_posterior_json TEXT,
            p_posterior REAL,
            alpha REAL,
            agreement TEXT,
            n_edges_found INTEGER,
            n_edges_after_fdr INTEGER,
            rejection_stage TEXT,
            availability_status TEXT,
            -- P2 (PLAN_v3 §6.P2 stage 3): MarketPhase axis A tag for
            -- decision-time cohort attribution. Additive, default NULL
            -- for legacy rows; legacy-DB ALTER TABLE migration below.
            market_phase TEXT,
            -- A5 (PLAN.md §A5 + Bug review Finding F): MarketPhaseEvidence
            -- provenance fields. ``market_phase_source`` distinguishes
            -- verified_gamma / fallback_f1 / onchain_resolved / unknown so
            -- attribution reports can stratify by determination quality.
            -- The 3 timestamp columns capture WHICH boundaries the phase
            -- was computed against — so a future cohort report can detect
            -- a midnight-straddle drift without re-running the cycle.
            -- ``uma_resolved_source`` carries the on-chain Settle tx hash
            -- when phase_source == "onchain_resolved", NULL otherwise.
            market_phase_source TEXT,
            market_start_at TEXT,
            market_end_at TEXT,
            settlement_day_entry_utc TEXT,
            uma_resolved_source TEXT,
            -- LIVE-PROB-P0 (2026-05-23 SCHEMA_VERSION 34): cumulative tail-mass evidence.
            -- prob_tail_mass_cal: sum(p_cal[left-tail bins]) at gate evaluation time.
            -- prob_tail_mass_market: sum(p_market[left-tail bins]) at gate evaluation time.
            -- prob_tail_entropy: Shannon entropy of p_cal distribution at decision time
            --   (nats; H = -sum(p * log(p)), 0 for degenerate point mass).
            -- All three are NULL for decisions that predate LIVE-PROB-P0 (legacy rows)
            -- and for decisions where market_prices were unavailable.
            -- ALTER TABLE migration below handles legacy DBs.
            prob_tail_mass_cal REAL,
            prob_tail_mass_market REAL,
            prob_tail_entropy REAL,
            -- Continuous re-decision P1 belief cache (resurrection 2026-06-12): per-bin executable
            -- condition_id (parallel to bin_labels_json) for the synthesized 'edli_belief:' rows.
            -- NULL for every non-belief / legacy row.
            condition_ids_json TEXT,
            -- Continuous re-decision conservative screen: per-bin q_lcb for YES and NO sides,
            -- parallel to bin_labels_json. NULL means no live entry-admission proof.
            q_lcb_yes_json TEXT,
            q_lcb_no_json TEXT,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_probability_trace_city_target
            ON probability_trace_fact(city, target_date, recorded_at);
        CREATE INDEX IF NOT EXISTS idx_probability_trace_snapshot
            ON probability_trace_fact(decision_snapshot_id);
        -- NB: idx_probability_trace_market_phase lives in the ALTER block
        -- below (must be created AFTER the ALTER TABLE adds the column on
        -- legacy DBs; fresh DBs hit the same path through the
        -- duplicate-column-swallowed retry).

        -- Selection-family facts for active candidate-family FDR accounting.
        CREATE TABLE IF NOT EXISTS selection_family_fact (
            family_id TEXT PRIMARY KEY,
            cycle_mode TEXT NOT NULL,
            decision_snapshot_id TEXT,
            city TEXT,
            target_date TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            created_at TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            decision_time_status TEXT
        );

        CREATE TABLE IF NOT EXISTS selection_hypothesis_fact (
            hypothesis_id TEXT PRIMARY KEY,
            family_id TEXT NOT NULL,
            decision_id TEXT,
            candidate_id TEXT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            range_label TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
            p_value REAL,
            q_value REAL,
            ci_lower REAL,
            ci_upper REAL,
            edge REAL,
            tested INTEGER NOT NULL DEFAULT 1 CHECK (tested IN (0, 1)),
            passed_prefilter INTEGER NOT NULL DEFAULT 0 CHECK (passed_prefilter IN (0, 1)),
            selected_post_fdr INTEGER NOT NULL DEFAULT 0 CHECK (selected_post_fdr IN (0, 1)),
            rejection_stage TEXT,
            recorded_at TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            FOREIGN KEY(family_id) REFERENCES selection_family_fact(family_id)
        );
        CREATE INDEX IF NOT EXISTS idx_selection_hypothesis_family
            ON selection_hypothesis_fact(family_id, selected_post_fdr, p_value);

        -- Model evaluation and promotion substrate. Behavior-neutral until a
        -- future packet wires active model selection through promotion state.



        -- Append-only trade chronicle
        -- env column: added via ALTER TABLE in init_schema lines ~854-859 — see chronicler.py:76
        CREATE TABLE IF NOT EXISTS chronicle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            trade_id INTEGER,
            timestamp TEXT NOT NULL,
            details_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_chronicle_dedup
          ON chronicle(trade_id, event_type);

        -- position_events is canonical-only (see apply_architecture_kernel_schema)

        -- Derived health view for PnL and edge compression
        CREATE TABLE IF NOT EXISTS strategy_health (
            strategy_key TEXT NOT NULL,
            as_of TEXT NOT NULL,
            open_exposure_usd REAL NOT NULL DEFAULT 0,
            settled_trades_30d INTEGER NOT NULL DEFAULT 0,
            realized_pnl_30d REAL NOT NULL DEFAULT 0,
            unrealized_pnl REAL NOT NULL DEFAULT 0,
            win_rate_30d REAL,
            brier_30d REAL,
            fill_rate_14d REAL,
            edge_trend_30d REAL,
            risk_level TEXT,
            execution_decay_flag INTEGER NOT NULL DEFAULT 0 CHECK (execution_decay_flag IN (0, 1)),
            edge_compression_flag INTEGER NOT NULL DEFAULT 0 CHECK (edge_compression_flag IN (0, 1)),
            PRIMARY KEY (strategy_key, as_of)
        );

        -- Decision chain: every cycle's artifacts (Blueprint v2 §3)
        CREATE TABLE IF NOT EXISTS decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            artifact_json TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decision_log_ts ON decision_log(timestamp);

        -- T1 Phase-1: decision_events — natural-key instrumentation table (Path D v3)
        -- PK: 5-component natural key; condition_id is nullable enrichment (NOT in PK)
        -- decision_event_id: deid_v1_ namespace — DISTINCT from dgid_v1_ (calibration)
        -- Writer computes hash via decision_event_id_v1_hash(); trigger backstop on NULL.
        -- SCHEMA_VERSION 13 (2026-05-19 T1)
        CREATE TABLE IF NOT EXISTS decision_events (
            market_slug         TEXT NOT NULL,
            temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
            target_date         TEXT NOT NULL,
            observation_time    TEXT NOT NULL,
            decision_seq        INTEGER NOT NULL,
            condition_id        TEXT,
            decision_event_id   TEXT,
            decision_time       TEXT NOT NULL,
            outcome             TEXT NOT NULL,
            side                TEXT NOT NULL,
            strategy_key        TEXT NOT NULL,
            cycle_id            TEXT,
            cycle_iteration     INTEGER,
            p_posterior         REAL,
            edge                REAL,
            target_size_usd     REAL,
            target_price        REAL,
            forecast_time              TEXT,
            provider_reported_time     TEXT,
            observation_available_at   TEXT NOT NULL,
            polymarket_end_anchor_source TEXT NOT NULL CHECK (
                polymarket_end_anchor_source IN ('gamma_explicit', 'f1_12z_fallback', 'unknown_legacy')
            ),
            first_member_observed_time TEXT,
            run_complete_time          TEXT,
            zeus_submit_intent_time    TEXT,
            venue_ack_time             TEXT,
            first_inclusion_block_time TEXT,
            finality_confirmed_time    TEXT,
            clock_skew_estimate_ms_at_submit INTEGER,
            raw_orderbook_hash_transition_delta_ms INTEGER,
            schema_version INTEGER NOT NULL CHECK (schema_version IN (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)),
            source         TEXT NOT NULL CHECK (source IN ('phase0_backfill', 'live_decision', 'offline_decision')),
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        );
        CREATE INDEX IF NOT EXISTS idx_decision_events_slug_date
            ON decision_events(market_slug, target_date);
        CREATE INDEX IF NOT EXISTS idx_decision_events_strategy
            ON decision_events(strategy_key, decision_time);
        CREATE INDEX IF NOT EXISTS idx_decision_events_event_id
            ON decision_events(decision_event_id);
        CREATE TRIGGER IF NOT EXISTS decision_events_event_id_backstop
        AFTER INSERT ON decision_events
        FOR EACH ROW
        WHEN NEW.decision_event_id IS NULL
        BEGIN
            UPDATE decision_events
               SET decision_event_id = 'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'
             WHERE market_slug = NEW.market_slug
               AND temperature_metric = NEW.temperature_metric
               AND target_date = NEW.target_date
               AND observation_time = NEW.observation_time
               AND decision_seq = NEW.decision_seq;
        END;

        -- ETL tables: legacy-predecessor data validated and imported

        -- Ladder backfill: 5 models × 7 leads per settlement
        CREATE TABLE IF NOT EXISTS forecast_skill (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            lead_days INTEGER NOT NULL,
            forecast_temp REAL NOT NULL,
            actual_temp REAL NOT NULL,
            error REAL NOT NULL,
            temp_unit TEXT NOT NULL,
            season TEXT NOT NULL,
            available_at TEXT NOT NULL,
            UNIQUE(city, target_date, source, lead_days)
        );

        -- Forecast error distribution substrate for future uncertainty correction.

        -- Per-model bias correction
        CREATE TABLE IF NOT EXISTS model_bias (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            source TEXT NOT NULL,
            bias REAL NOT NULL,
            mae REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            discount_factor REAL DEFAULT 0.7,
            UNIQUE(city, season, source)
        );


        -- DST-safe hourly observation timeline.
        -- CONSOLIDATION 2026-05-29: the legacy subset DDL for observation_instants
        -- (22 cols) was DELETED here. The canonical superset (32 cols: authority,
        -- data_version, provenance_json, running_min, identity-spine, +bounds CHECK)
        -- is now the ONLY definition of observation_instants and lives in
        -- src/state/schema/v2_schema.py (applied via apply_canonical_schema, called
        -- from init_schema). Defining a subset here would mask the superset under
        -- CREATE TABLE IF NOT EXISTS (init_schema runs this executescript BEFORE
        -- apply_canonical_schema), so it MUST stay deleted. Indexes moved to
        -- v2_schema.py as well (idx_observation_instants_city_ts).

        -- Daily sunrise/sunset context for Day0 and DST-aware timing
        CREATE TABLE IF NOT EXISTS solar_daily (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            timezone TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            sunrise_local TEXT NOT NULL,
            sunset_local TEXT NOT NULL,
            sunrise_utc TEXT NOT NULL,
            sunset_utc TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL,
            dst_active INTEGER NOT NULL,
            UNIQUE(city, target_date)
        );

        -- Diurnal temperature curves per city×season
        CREATE TABLE IF NOT EXISTS diurnal_curves (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            hour INTEGER NOT NULL,
            avg_temp REAL NOT NULL,
            std_temp REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            p_high_set REAL,
            UNIQUE(city, season, hour)
        );

        CREATE TABLE IF NOT EXISTS diurnal_peak_prob (
            city TEXT NOT NULL,
            month INTEGER NOT NULL,
            hour INTEGER NOT NULL,
            p_high_set REAL NOT NULL,
            n_obs INTEGER NOT NULL,
            UNIQUE(city, month, hour)
        );

        -- Day0 residual learning substrate.
        -- Behavior-neutral: current Day0Signal hard-floor runtime remains active.

        -- Raw forecast source rows. New-city onboarding writes here first;
        -- skill/bias/profile tables are derived from this table plus settlements.
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            forecast_basis_date TEXT,
            forecast_issue_time TEXT,
            lead_days INTEGER,
            lead_time_hours REAL,
            forecast_high REAL,
            forecast_low REAL,
            temp_unit TEXT DEFAULT 'F',
            retrieved_at TEXT,
            imported_at TEXT,
            source_id TEXT,
            raw_payload_hash TEXT,
            captured_at TEXT,
            authority_tier TEXT,
            rebuild_run_id TEXT,
            data_source_version TEXT,
            availability_provenance TEXT
                CHECK (availability_provenance IS NULL
                       OR availability_provenance IN ('derived_dissemination', 'fetch_time', 'reconstructed', 'recorded')),
            UNIQUE(city, target_date, source, forecast_basis_date)
        );
        CREATE INDEX IF NOT EXISTS idx_forecasts_city_date
            ON forecasts(city, target_date);



        -- Day-over-day temperature persistence
        CREATE TABLE IF NOT EXISTS temp_persistence (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            delta_bucket TEXT NOT NULL,
            frequency REAL NOT NULL,
            avg_next_day_reversion REAL,
            n_samples INTEGER NOT NULL,
            UNIQUE(city, season, delta_bucket)
        );

        -- Create indexes for common query patterns
        CREATE INDEX IF NOT EXISTS idx_settlements_city_date
            ON settlements(city, target_date);
        CREATE INDEX IF NOT EXISTS idx_observations_city_date
            ON observations(city, target_date, source);
        CREATE INDEX IF NOT EXISTS idx_daily_observation_revisions_lookup
            ON daily_observation_revisions(city, target_date, source, recorded_at);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_observation_revisions_payload
            ON daily_observation_revisions(
                city, target_date, source, incoming_combined_payload_hash, reason
            );
        -- idx_observation_instants_* moved to v2_schema.py with the canonical
        -- (superset) table DDL in the 2026-05-29 consolidation. See note at the
        -- former observation_instants subset DDL site above.
        CREATE INDEX IF NOT EXISTS idx_token_price_token
            ON token_price_log(token_id, timestamp);
        -- idx_market_events_slug removed in B3cont (PR3): bare market_events shell dropped.
        -- v1.F20: idx_ensemble_city_date removed (ensemble_snapshots table dropped).
        -- idx_calibration_bucket removed in B3 (PR3): bare calibration_pairs shell dropped.

        -- K2 data-coverage index — the immune system's memory for live data ingestion.
        -- One row per expected (data_table × city × data_source × target_date × sub_key);
        -- live appenders flip rows to WRITTEN, scanners write MISSING for unrecorded
        -- expected rows, and known exceptions (HKO incomplete-flag days, UKMO pre-start,
        -- new-city onboard lag) are pinned as LEGITIMATE_GAP so the scanner won't
        -- keep re-attempting them. Distinct from `availability_fact` which logs
        -- runtime cycle/order outages — this table is specifically a data-ingestion
        -- coverage ledger.
        CREATE TABLE IF NOT EXISTS data_coverage (
            data_table  TEXT NOT NULL
                CHECK (data_table IN ('observations','observation_instants','solar_daily','forecasts')),
            city        TEXT NOT NULL,
            data_source TEXT NOT NULL,
            target_date TEXT NOT NULL,
            sub_key     TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL
                CHECK (status IN ('WRITTEN','LEGITIMATE_GAP','FAILED','MISSING')),
            reason      TEXT,
            fetched_at  TEXT NOT NULL,
            expected_at TEXT,
            retry_after TEXT,
            PRIMARY KEY (data_table, city, data_source, target_date, sub_key)
        );
        CREATE INDEX IF NOT EXISTS idx_data_coverage_status
            ON data_coverage(status, data_table);
        CREATE INDEX IF NOT EXISTS idx_data_coverage_scan
            ON data_coverage(data_table, city, data_source, target_date);
        CREATE INDEX IF NOT EXISTS idx_data_coverage_retry
            ON data_coverage(status, retry_after) WHERE status = 'FAILED';

        -- PR45b data-daemon readiness provenance substrate. These tables are
        -- behavior-neutral until later phases wire ingest writers and runtime
        -- consumers through their repo modules.
        CREATE TABLE IF NOT EXISTS job_run (
            job_run_id TEXT PRIMARY KEY,
            job_run_key TEXT NOT NULL UNIQUE,
            job_name TEXT NOT NULL,
            plane TEXT NOT NULL CHECK (plane IN (
                'forecast','observation','solar_aux','market_topology',
                'quote','settlement_truth','source_health','hole_backfill','telemetry_control'
            )),
            scheduled_for TEXT NOT NULL,
            missed_from TEXT,
            started_at TEXT,
            finished_at TEXT,
            lock_key TEXT,
            lock_acquired_at TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED','SKIPPED_LOCK_HELD'
            )),
            reason_code TEXT,
            rows_written INTEGER NOT NULL DEFAULT 0,
            rows_failed INTEGER NOT NULL DEFAULT 0,
            source_run_id TEXT,
            source_id TEXT,
            track TEXT,
            release_calendar_key TEXT,
            safe_fetch_not_before TEXT,
            expected_scope_json TEXT NOT NULL DEFAULT '{}',
            affected_scope_json TEXT NOT NULL DEFAULT '{}',
            readiness_impacts_json TEXT NOT NULL DEFAULT '[]',
            readiness_recomputed_at TEXT,
            meta_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(job_name, scheduled_for, source_id, track, release_calendar_key)
        );
        CREATE INDEX IF NOT EXISTS idx_job_run_job_window
            ON job_run(job_name, scheduled_for);
        CREATE INDEX IF NOT EXISTS idx_job_run_plane_status
            ON job_run(plane, status, scheduled_for);
        CREATE INDEX IF NOT EXISTS idx_job_run_source_run
            ON job_run(source_run_id);

        CREATE TABLE IF NOT EXISTS source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            track TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            ingest_mode TEXT NOT NULL CHECK (ingest_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            origin_mode TEXT NOT NULL CHECK (origin_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            source_cycle_time TEXT NOT NULL,
            source_issue_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            fetch_started_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            imported_at TEXT,
            valid_time_start TEXT,
            valid_time_end TEXT,
            target_local_date TEXT,
            city_id TEXT,
            city_timezone TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            dataset_id TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            expected_steps_json TEXT NOT NULL DEFAULT '[]',
            observed_steps_json TEXT NOT NULL DEFAULT '[]',
            expected_count INTEGER,
            observed_count INTEGER,
            completeness_status TEXT NOT NULL CHECK (completeness_status IN (
                'COMPLETE','PARTIAL','MISSING','NOT_RELEASED'
            )),
            partial_run INTEGER NOT NULL DEFAULT 0 CHECK (partial_run IN (0,1)),
            raw_payload_hash TEXT,
            manifest_hash TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED'
            )),
            reason_code TEXT,
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            CHECK (partial_run = 0 OR completeness_status = 'PARTIAL')
        );
        CREATE INDEX IF NOT EXISTS idx_source_run_source_cycle
            ON source_run(source_id, track, source_cycle_time);
        CREATE INDEX IF NOT EXISTS idx_source_run_scope
            ON source_run(city_id, city_timezone, target_local_date, temperature_metric, dataset_id);
        CREATE INDEX IF NOT EXISTS idx_source_run_status
            ON source_run(status, completeness_status, source_cycle_time);

        CREATE TABLE IF NOT EXISTS source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_transport TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            city_id TEXT NOT NULL,
            city TEXT NOT NULL,
            city_timezone TEXT NOT NULL,
            target_local_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
            physical_quantity TEXT NOT NULL,
            observation_field TEXT NOT NULL,
            data_version TEXT NOT NULL,
            expected_members INTEGER NOT NULL,
            observed_members INTEGER NOT NULL,
            expected_steps_json TEXT NOT NULL,
            observed_steps_json TEXT NOT NULL,
            snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            target_window_start_utc TEXT NOT NULL,
            target_window_end_utc TEXT NOT NULL,
            completeness_status TEXT NOT NULL CHECK (completeness_status IN (
                'COMPLETE','PARTIAL','MISSING','HORIZON_OUT_OF_RANGE','NOT_RELEASED'
            )),
            readiness_status TEXT NOT NULL CHECK (readiness_status IN (
                'LIVE_ELIGIBLE','BLOCKED','UNKNOWN_BLOCKED'
            )),
            reason_code TEXT,
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(
                source_run_id, source_id, source_transport, release_calendar_key,
                track, city_id, city_timezone, target_local_date,
                temperature_metric, data_version
            )
        );
        CREATE INDEX IF NOT EXISTS idx_source_run_coverage_scope
            ON source_run_coverage(city_id, city_timezone, target_local_date, temperature_metric, source_id, source_transport, data_version);
        CREATE INDEX IF NOT EXISTS idx_source_run_coverage_status
            ON source_run_coverage(readiness_status, completeness_status, computed_at);

        CREATE TABLE IF NOT EXISTS readiness_state (
            readiness_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            scope_type TEXT NOT NULL CHECK (scope_type IN (
                'global','source','city_metric','market','strategy','quote'
            )),
            city_id TEXT,
            city TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            metric TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            source_id TEXT,
            track TEXT,
            source_run_id TEXT,
            market_family TEXT,
            event_id TEXT,
            condition_id TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            strategy_key TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'READY','LIVE_ELIGIBLE','BLOCKED','UNKNOWN_BLOCKED'
            )),
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            dependency_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(
                scope_type, city_id, city_timezone, target_local_date,
                temperature_metric, physical_quantity, observation_field,
                data_version, strategy_key, market_family, source_id, track,
                condition_id
            )
        );
        CREATE INDEX IF NOT EXISTS idx_readiness_state_entry_scope
            ON readiness_state(city_id, city_timezone, target_local_date, temperature_metric, strategy_key, market_family, condition_id);
        CREATE INDEX IF NOT EXISTS idx_readiness_state_status_expiry
            ON readiness_state(status, expires_at);

        CREATE TABLE IF NOT EXISTS market_topology_state (
            topology_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            market_family TEXT NOT NULL,
            event_id TEXT,
            condition_id TEXT NOT NULL,
            question_id TEXT,
            city_id TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            bin_topology_hash TEXT,
            gamma_captured_at TEXT,
            gamma_updated_at TEXT,
            source_contract_status TEXT NOT NULL CHECK (source_contract_status IN (
                'MATCH','MISMATCH','UNKNOWN','QUARANTINED'
            )),
            source_contract_reason TEXT,
            authority_status TEXT NOT NULL CHECK (authority_status IN (
                'VERIFIED','STALE','FETCH_FAILED_NO_CACHE',
                'KEYWORD_DISCOVERY_UNVERIFIED','UNKNOWN'
            )),
            status TEXT NOT NULL CHECK (status IN (
                'CURRENT','STALE','FETCH_FAILED_NO_CACHE',
                'KEYWORD_DISCOVERY_UNVERIFIED','MISMATCH','UNKNOWN'
            )),
            expires_at TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(market_family, condition_id, city_id, target_local_date, temperature_metric, data_version)
        );
        CREATE INDEX IF NOT EXISTS idx_market_topology_scope
            ON market_topology_state(city_id, city_timezone, target_local_date, temperature_metric, market_family, condition_id);
        CREATE INDEX IF NOT EXISTS idx_market_topology_status_expiry
            ON market_topology_state(status, expires_at);

        CREATE TABLE IF NOT EXISTS source_contract_audit_events (
            audit_id TEXT PRIMARY KEY,
            checked_at_utc TEXT NOT NULL,
            scan_authority TEXT NOT NULL CHECK (scan_authority IN (
                'VERIFIED','FIXTURE','STALE_CACHE','FETCH_FAILED_NO_CACHE',
                'KEYWORD_DISCOVERY_UNVERIFIED','NEVER_FETCHED'
            )),
            report_status TEXT CHECK (report_status IS NULL OR report_status IN (
                'OK','WARN','ALERT','DATA_UNAVAILABLE'
            )),
            severity TEXT NOT NULL CHECK (severity IN ('OK','WARN','ALERT','DATA_UNAVAILABLE')),
            event_id TEXT,
            slug TEXT,
            title TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            source_contract_status TEXT NOT NULL CHECK (source_contract_status IN (
                'MATCH','MISSING','AMBIGUOUS','MISMATCH','UNSUPPORTED','UNKNOWN','QUARANTINED'
            )),
            source_contract_reason TEXT,
            configured_source_family TEXT,
            configured_station_id TEXT,
            observed_source_family TEXT,
            observed_station_id TEXT,
            resolution_sources_json TEXT NOT NULL DEFAULT '[]',
            source_contract_json TEXT NOT NULL DEFAULT '{}',
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_source_contract_audit_city_date
            ON source_contract_audit_events(city, target_date, temperature_metric, checked_at_utc);
        CREATE INDEX IF NOT EXISTS idx_source_contract_audit_status
            ON source_contract_audit_events(source_contract_status, severity, checked_at_utc);
        CREATE TRIGGER IF NOT EXISTS source_contract_audit_events_no_update
        BEFORE UPDATE ON source_contract_audit_events
        BEGIN
          SELECT RAISE(ABORT, 'source_contract_audit_events is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS source_contract_audit_events_no_delete
        BEFORE DELETE ON source_contract_audit_events
        BEGIN
          SELECT RAISE(ABORT, 'source_contract_audit_events is append-only');
        END;

        -- Availability/outage fact log (observability — kernel §availability_fact)
        CREATE TABLE IF NOT EXISTS availability_fact (
            availability_id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('cycle', 'candidate', 'city_target', 'order', 'chain')),
            scope_key TEXT NOT NULL,
            failure_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            impact TEXT NOT NULL CHECK (impact IN ('skip', 'degrade', 'retry', 'block')),
            details_json TEXT NOT NULL
);

        -- P1.S1 (INV-28 / D-P1-1-a, D-P1-2-a): durable command journal
        -- venue_commands is the pre-side-effect persistence layer for every
        -- place_limit_order / cancel call.  Written via src/state/venue_command_repo.py
        -- only — no direct SQL outside the repo module.
        CREATE TABLE IF NOT EXISTS venue_commands (
            command_id TEXT PRIMARY KEY,
            -- U1 (INV-NEW-E): every persisted venue command cites an
            -- executable-market snapshot. Freshness/tradability are enforced
            -- in src/state/venue_command_repo.py because they depend on now().
            snapshot_id TEXT NOT NULL,
            -- U2 (INV-NEW-F): every venue command cites a pre-side-effect
            -- submission provenance envelope.
            envelope_id TEXT NOT NULL,
            -- Identity
            position_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_kind TEXT NOT NULL,
            -- Order shape
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            price REAL NOT NULL,
            -- Venue identity (NULL until first ACK)
            venue_order_id TEXT,
            -- Lifecycle
            state TEXT NOT NULL,
            last_event_id TEXT,
            -- Timestamps
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            -- Optional review
            review_required_reason TEXT,
            -- SCH-W1.2-ORDER-STATE: decision-basis stamp. Nullable, write-once at
            -- insert_command. = forecast_posteriors.posterior_identity_hash at
            -- decision time; NULL BY RULE for non-decision-basis commands
            -- (exchange_reconcile backfills) and legacy rows.
            q_version TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_venue_commands_position ON venue_commands(position_id);
        CREATE INDEX IF NOT EXISTS idx_venue_commands_state ON venue_commands(state);
        CREATE INDEX IF NOT EXISTS idx_venue_commands_decision ON venue_commands(decision_id);

        -- P1.S1 (INV-28 / D-P1-3-a): append-only event log for venue_commands.
        -- Records every state transition.  NC-18 forbids UPDATE/DELETE outside
        -- src/state/venue_command_repo.py.
        CREATE TABLE IF NOT EXISTS venue_command_events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT,
            state_after TEXT NOT NULL,
            UNIQUE (command_id, sequence_no)
        );

        CREATE INDEX IF NOT EXISTS idx_venue_command_events_command ON venue_command_events(command_id);
        CREATE INDEX IF NOT EXISTS idx_venue_command_events_type ON venue_command_events(event_type);

    """)
    _ensure_job_run_release_key_identity(conn)
    init_snapshot_schema(conn, include_latest=False)
    init_collateral_schema(conn)
    # R3 M4 exit mutex DDL lives here to keep DB initialization independent of
    # importing src.execution modules.  The execution module repeats the same
    # idempotent CREATE TABLE for direct use.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS exit_mutex_holdings (
          mutex_key TEXT PRIMARY KEY,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
          acquired_at TEXT NOT NULL,
          released_at TEXT,
          release_reason TEXT
        );
    """)
    # R3 M5 exchange reconciliation findings.  Schema stays in state/db.py so
    # DB initialization does not import the execution sweep module.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS exchange_reconcile_findings (
          finding_id TEXT PRIMARY KEY,
          kind TEXT NOT NULL CHECK (kind IN (
            'exchange_ghost_order','local_orphan_order','unrecorded_trade',
            'position_drift','heartbeat_suspected_cancel','cutover_wipe',
            'collateral_identity_mismatch'
          )),
          subject_id TEXT NOT NULL,
          context TEXT NOT NULL CHECK (context IN (
            'periodic','ws_gap','heartbeat_loss','cutover','operator'
          )),
          evidence_json TEXT NOT NULL,
          recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
          resolved_at TEXT,
          resolution TEXT,
          resolved_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_findings_unresolved
          ON exchange_reconcile_findings (resolved_at)
          WHERE resolved_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_findings_unresolved_subject
          ON exchange_reconcile_findings (kind, subject_id, context)
          WHERE resolved_at IS NULL;
    """)
    init_provenance_projection_schema(conn)
    # Keep wrap/unwrap DDL local to the schema owner so src.state does not
    # import src.execution during DB initialization. The execution module owns
    # the command API and repeats the same idempotent DDL for direct use.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wrap_unwrap_commands (
          command_id TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          direction TEXT NOT NULL CHECK (direction IN ('WRAP','UNWRAP')),
          amount_micro INTEGER NOT NULL,
          tx_hash TEXT,
          block_number INTEGER,
          confirmation_count INTEGER DEFAULT 0,
          requested_at TEXT NOT NULL,
          terminal_at TEXT,
          error_payload TEXT
        );

        CREATE TABLE IF NOT EXISTS wrap_unwrap_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          command_id TEXT NOT NULL REFERENCES wrap_unwrap_commands(command_id),
          event_type TEXT NOT NULL,
          payload_json TEXT,
          recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
        );
    """)
    # T1A: DDL single-source — delegate to schema owner to avoid duplication.
    # init_settlement_command_schema() already calls executescript(SETTLEMENT_COMMAND_SCHEMA)
    # internally; calling executescript here too is redundant. Use only the delegated call.
    from src.execution.settlement_commands import init_settlement_command_schema
    # PR 3+6 (2026-05-19): idempotent column migrations for settlement_commands.
    init_settlement_command_schema(conn)

    # PR 6 (2026-05-19): chain-finality split columns on wrap_unwrap_commands.
    for _alter_sql in [
        "ALTER TABLE wrap_unwrap_commands ADD COLUMN first_inclusion_block_time TEXT",
        "ALTER TABLE wrap_unwrap_commands ADD COLUMN finality_confirmed_time TEXT",
    ]:
        try:
            conn.execute(_alter_sql)
        except Exception as _exc:
            if "duplicate column" not in str(_exc).lower():
                raise

    # task #200 (2026-05-10): executescript() resets the C-level busy handler.
    # Re-apply after the last executescript() so all subsequent conn.execute()
    # calls (ALTER loops, apply_canonical_schema) wait under contention instead of
    # failing immediately. apply_canonical_schema also sets this independently as a
    # belt-and-suspenders guard for callers that bypass init_schema.
    conn.execute(f"PRAGMA busy_timeout = {int(os.environ.get('ZEUS_DB_BUSY_TIMEOUT_MS', '30000'))}")

    # Safe Schema evolution for phase 3 attribution
    for col in ["entry_alpha_usd", "execution_slippage_usd", "exit_timing_usd", "risk_throttling_usd", "settlement_edge_usd"]:
        try:
            conn.execute(f"ALTER TABLE trade_decisions ADD COLUMN {col} REAL DEFAULT 0.0;")
        except sqlite3.OperationalError:
            pass

    # P2 (PLAN_v3 §6.P2 stage 3, 2026-05-04): probability_trace_fact gains
    # ``market_phase`` for decision-time cohort attribution. Legacy DBs
    # predate this column; CREATE TABLE IF NOT EXISTS would no-op so the
    # writer at log_probability_trace_fact would fail with
    # "table probability_trace_fact has no column named market_phase".
    # ALTER TABLE catches legacy DBs; OperationalError on duplicate-column
    # is swallowed for fresh DBs.
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN market_phase TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        # Read consumer for this index lands with PLAN_v3 §6.P9 (per-(strategy_key,
        # market_phase) cohort attribution SQL). Until then the index has no live
        # query; it is provisioned now so the first cohort report doesn't trigger
        # a full-table scan after months of writes. Do NOT GC as orphan — see
        # critic R3 ATTACK 9 (PR #53).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_probability_trace_market_phase "
            "ON probability_trace_fact(market_phase);"
        )
    except sqlite3.OperationalError:
        pass

    # A5 (PLAN.md §A5 + Bug review Finding F, 2026-05-04): MarketPhaseEvidence
    # provenance columns. Same migration pattern as ``market_phase`` above —
    # ALTER catches legacy DBs; duplicate-column OperationalError is the
    # expected fresh-DB no-op path.
    for col in (
        "market_phase_source",
        "market_start_at",
        "market_end_at",
        "settlement_day_entry_utc",
        "uma_resolved_source",
    ):
        try:
            conn.execute(
                f"ALTER TABLE probability_trace_fact ADD COLUMN {col} TEXT;"
            )
        except sqlite3.OperationalError:
            pass
    try:
        # Index on phase_source for cohort queries that group by determination
        # quality (e.g., "what % of post-A5 decisions used fallback_f1?").
        # Provisioned now so the first cohort report after wiring doesn't
        # trigger a full-table scan; mirrors the idx_probability_trace_market_phase
        # rationale from §6.P9.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_probability_trace_phase_source "
            "ON probability_trace_fact(market_phase_source);"
        )
    except sqlite3.OperationalError:
        pass

    # Continuous re-decision P1 belief cache (resurrection 2026-06-12): the synthesized
    # 'edli_belief:' rows store the per-bin executable condition_id (parallel to bin_labels_json)
    # so the P2 screen can join a cached belief to the freshest executable_market_snapshots row.
    # Additive, NULL for every legacy row; column-subset-safe per assert_db_matches_registry.
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN condition_ids_json TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN temperature_metric TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN q_lcb_yes_json TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN q_lcb_no_json TEXT;")
    except sqlite3.OperationalError:
        pass

    # LIVE-PROB-P0 (SCHEMA_VERSION 34, 2026-05-23): probability_trace_fact gains
    # three tail-evidence columns for the cumulative tail-mass discrepancy gate.
    # Same ALTER TABLE pattern as ``market_phase`` / A5 above — fresh DBs hit the
    # duplicate-column OperationalError which is swallowed; legacy DBs get the
    # columns added. All three default NULL for pre-LIVE-PROB-P0 rows.
    for col in (
        "prob_tail_mass_cal REAL",
        "prob_tail_mass_market REAL",
        "prob_tail_entropy REAL",
    ):
        try:
            conn.execute(
                f"ALTER TABLE probability_trace_fact ADD COLUMN {col};"
            )
        except sqlite3.OperationalError:
            pass

    # LIVE-PROB-P0 §E (SCHEMA_VERSION 35, 2026-05-23): probability_trace_fact gains
    # 11 edge-bin sanity telemetry columns per operator binding spec §E.
    # All columns populated in production when per-edge gate is evaluated (non-day0).
    # NULL for legacy rows and day0 decisions (gate not called for day0 by design).
    for col in (
        "probability_sanity_mode TEXT",
        "probability_sanity_reason TEXT",
        "edge_bin_idx INTEGER",
        "edge_bin_label TEXT",
        "edge_bin_p_raw REAL",
        "edge_bin_p_cal REAL",
        "edge_bin_p_market REAL",
        "edge_bin_member_support REAL",
        "edge_bin_odds_ratio REAL",
        "near_tail_p_cal REAL",
        "near_tail_p_market REAL",
    ):
        try:
            conn.execute(
                f"ALTER TABLE probability_trace_fact ADD COLUMN {col};"
            )
        except sqlite3.OperationalError:
            pass

    # A5 uma_resolution table — listener writes here, cycle_runtime reads
    # it via uma_resolution_listener.lookup_resolution. Idempotent.
    from src.state.uma_resolution_listener import init_uma_resolution_schema as _init_uma
    _init_uma(conn)

    # REOPEN-1 (2026-04-23): forecasts writer at src/data/forecasts_append.py:256-262
    # inserts rebuild_run_id + data_source_version; legacy DBs predate the CREATE
    # TABLE declaration of these two columns, so CREATE TABLE IF NOT EXISTS no-ops
    # and the writer fails at runtime with "table forecasts has no column named
    # rebuild_run_id" (observed: k2_forecasts_daily FAILED every 30 min per
    # state/scheduler_jobs_health.json). ALTER path catches legacy DBs without
    # disturbing fresh DBs (OperationalError on duplicate-column is swallowed).
    for col in [
        "rebuild_run_id",
        "data_source_version",
        "source_id",
        "raw_payload_hash",
        "captured_at",
        "authority_tier",
    ]:
        try:
            conn.execute(f"ALTER TABLE forecasts ADD COLUMN {col} TEXT;")
        except sqlite3.OperationalError:
            pass

    # F11 (2026-04-28): forecasts writer at src/data/forecasts_append.py:267-274 now
    # inserts availability_provenance (D4 antibody). Same pattern as REOPEN-1 above:
    # CREATE TABLE adds the column for fresh DBs, this ALTER catches legacy DBs.
    # The CHECK constraint can't be added via ALTER in SQLite, so legacy DBs run
    # without the DB-level enum enforcement; the writer-level assertion at
    # forecasts_append.py:283-288 still rejects bad values. Fresh DBs get both.
    try:
        conn.execute("ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT;")
    except sqlite3.OperationalError:
        pass

    # U1: legacy trade DBs predate the executable snapshot citation. SQLite
    # cannot add a NOT NULL column without a table rebuild, so old DBs get the
    # nullable column while venue_command_repo.insert_command enforces it for
    # every new command row.
    try:
        conn.execute("ALTER TABLE venue_commands ADD COLUMN snapshot_id TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_venue_commands_snapshot ON venue_commands(snapshot_id);")

    # B3cont: ALTER TABLE platt_models (bare) removed — table dropped.

    # Provenance: env column on trade-facing tables (Decision 2).
    # Existing non-event rows default to 'live' for legacy compatibility.
    _env_tables = ["trade_decisions", "chronicle", "decision_log"]
    for table in _env_tables:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN env TEXT NOT NULL DEFAULT 'live';")
        except sqlite3.OperationalError:
            pass  # Column already exists
    try:
        conn.execute("ALTER TABLE position_events ADD COLUMN env TEXT;")
    except sqlite3.OperationalError:
        pass  # Column already exists
            
    try:
        conn.execute("ALTER TABLE trade_decisions ADD COLUMN edge_source TEXT;")
    except sqlite3.OperationalError:
        pass

    # Backfill missing trade_decisions attribution / snapshot columns on older DBs.
    for ddl in [
        "ALTER TABLE trade_decisions ADD COLUMN runtime_trade_id TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN order_id TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN order_status_text TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN order_posted_at TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN entered_at_ts TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN chain_state TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN bin_type TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN discovery_mode TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN market_hours_open REAL;",
        "ALTER TABLE trade_decisions ADD COLUMN fill_quality REAL;",
        "ALTER TABLE trade_decisions ADD COLUMN strategy TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN entry_method TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN selected_method TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN applied_validations_json TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_trigger TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_reason TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN admin_exit_reason TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_divergence_score REAL DEFAULT 0.0;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_market_velocity_1h REAL DEFAULT 0.0;",
        "ALTER TABLE trade_decisions ADD COLUMN exit_forward_edge REAL DEFAULT 0.0;",
        "ALTER TABLE trade_decisions ADD COLUMN settlement_semantics_json TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN epistemic_context_json TEXT;",
        "ALTER TABLE trade_decisions ADD COLUMN edge_context_json TEXT;",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # calibration_pairs bare ALTER TABLE blocks removed in B3 (PR3):
    # bare calibration_pairs shell dropped; v2 schema owns the table.

    # P-B (2026-04-23): INV-14 identity spine + provenance vehicle on settlements.
    # Plan: docs/operations/task_2026-04-23_data_readiness_remediation/evidence/pb_schema_plan.md
    # All columns are nullable (pre-P-E rows may carry NULL); NOT-NULL enforcement is
    # deferred to P-E DELETE+INSERT reconstruction writers.
    for ddl in [
        "ALTER TABLE settlements ADD COLUMN pm_bin_lo REAL;",
        "ALTER TABLE settlements ADD COLUMN pm_bin_hi REAL;",
        "ALTER TABLE settlements ADD COLUMN unit TEXT;",
        "ALTER TABLE settlements ADD COLUMN settlement_source_type TEXT;",
        "ALTER TABLE settlements ADD COLUMN temperature_metric TEXT "
        "CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low'));",
        "ALTER TABLE settlements ADD COLUMN physical_quantity TEXT;",
        "ALTER TABLE settlements ADD COLUMN observation_field TEXT "
        "CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp'));",
        "ALTER TABLE settlements ADD COLUMN data_version TEXT;",
        "ALTER TABLE settlements ADD COLUMN provenance_json TEXT;",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    # REOPEN-2 (2026-04-24, data-readiness-tail): settlements UNIQUE migration.
    # Pre-REOPEN-2 schema: UNIQUE(city, target_date) — structurally blocks
    # dual-track (a HIGH row for city+date makes a LOW row for the same
    # city+date UNIQUE-collide). Per critic-opus P0.2 forensic-triage C3+C4,
    # this is a pre-flip BLOCKER for DR-33-C — first low-market settlement
    # attempt on flag-flip would silently drop the row and break the learning
    # chain for the LOW track.
    #
    # SQLite cannot ALTER a UNIQUE constraint; the only path is table
    # recreation. Idempotent: detect whether current table already has the
    # new UNIQUE(city, target_date, temperature_metric) via sqlite_master
    # SQL inspection; skip if yes.
    #
    # Safety: scratch-DB dry-run verified (2026-04-24) that the rebuild is
    # lossless on 1,561 rows + preserves authority groups (1469 VERIFIED + 92
    # QUARANTINED) + unlocks dual-track. Migration runs BEFORE trigger DROP+
    # CREATE blocks below so triggers install against the rebuilt table.
    #
    # B3cont (2026-05-28): this migration applies to world.db (authoritative settlements).
    # The bare settlements shell on forecasts.db has been dropped.
    try:
        settlements_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='settlements' AND type='table'"
        ).fetchone()
        settlements_sql = settlements_sql_row[0] if settlements_sql_row else ""
        needs_migration = (
            settlements_sql
            and "UNIQUE(city, target_date, temperature_metric)" not in settlements_sql
            and "UNIQUE (city, target_date, temperature_metric)" not in settlements_sql
        )
        if needs_migration:
            # Dynamic column-list copy (preserves schema even if future ALTERs
            # add more columns beyond the current set).
            cols = [r[1] for r in conn.execute("PRAGMA table_info(settlements)")]
            col_list = ", ".join(cols)
            pre_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
            conn.execute(
                """
                CREATE TABLE settlements_migrated (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    market_slug TEXT,
                    winning_bin TEXT,
                    settlement_value REAL,
                    settlement_source TEXT,
                    settled_at TEXT,
                    authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
                    pm_bin_lo REAL,
                    pm_bin_hi REAL,
                    unit TEXT,
                    settlement_source_type TEXT,
                    temperature_metric TEXT
                        CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
                    physical_quantity TEXT,
                    observation_field TEXT
                        CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
                    data_version TEXT,
                    provenance_json TEXT,
                    UNIQUE(city, target_date, temperature_metric)
                )
                """
            )
            conn.execute(
                f"INSERT INTO settlements_migrated ({col_list}) SELECT {col_list} FROM settlements"
            )
            post_count = conn.execute(
                "SELECT COUNT(*) FROM settlements_migrated"
            ).fetchone()[0]
            if post_count != pre_count:
                raise RuntimeError(
                    f"REOPEN-2 row-count drift: pre={pre_count} post={post_count} — "
                    "ABORT migration to prevent data loss"
                )
            conn.execute("DROP TABLE settlements")
            conn.execute("ALTER TABLE settlements_migrated RENAME TO settlements")
            # REOPEN-2 idempotency: DROP TABLE above silently drops
            # idx_settlements_city_date; recreate it inside this migration
            # block so REL-3b (legacy-DB second-run hash stability) holds.
            # Fresh DBs take this index via the executescript at db.py:1310.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_settlements_city_date"
                " ON settlements(city, target_date)"
            )
    except sqlite3.OperationalError:
        # Fresh DBs where settlements doesn't exist yet fall through to
        # CREATE TABLE IF NOT EXISTS above (which now declares new UNIQUE).
        # No action needed.
        pass

    # P-B authority-monotonic trigger (INV-FP-5 enforcement).
    # Reactivation contract: QUARANTINED->VERIFIED requires a top-level JSON key
    # `reactivated_by` that is a non-empty text value in provenance_json.
    # Substring LIKE is intentionally avoided to prevent false-positive matches
    # on keys like "not_reactivated_by". DROP + CREATE (not CREATE IF NOT EXISTS)
    # because S2.1 (2026-04-23 data-readiness-tail) extended the WHEN clause to
    # reject presence-only bypasses (reactivated_by=false / 0 / "" / {} / [])
    # — IF NOT EXISTS would silently retain the weaker v1 predicate on any DB
    # that had it. Idempotency preserved via DROP IF EXISTS.
    try:
        conn.execute("DROP TRIGGER IF EXISTS settlements_authority_monotonic")
        conn.execute(
            """
            CREATE TRIGGER settlements_authority_monotonic
            BEFORE UPDATE OF authority ON settlements
            WHEN (OLD.authority = 'VERIFIED' AND NEW.authority = 'UNVERIFIED')
              OR (OLD.authority = 'QUARANTINED' AND NEW.authority = 'VERIFIED'
                  AND (NEW.provenance_json IS NULL
                       OR json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL
                       OR json_type(NEW.provenance_json, '$.reactivated_by') != 'text'
                       OR length(json_extract(NEW.provenance_json, '$.reactivated_by')) = 0))
            BEGIN
                SELECT RAISE(ABORT, 'settlements.authority transition forbidden: VERIFIED->UNVERIFIED blocked, or QUARANTINED->VERIFIED requires provenance_json.reactivated_by to be a non-empty text value');
            END;
            """
        )
    except sqlite3.OperationalError:
        pass

    # POST-AUDIT FIX #1 (2026-04-24, adversarial-audit follow-up):
    # Close the NULL-NULL UNIQUE hole on settlements.
    #
    # REOPEN-2 (earlier today) installed UNIQUE(city, target_date,
    # temperature_metric). CHECK constraint at
    # `temperature_metric TEXT CHECK (temperature_metric IS NULL OR
    # temperature_metric IN ('high','low'))` intentionally tolerates NULL
    # so legacy-schema ALTER-added rows could pre-exist; SQLite UNIQUE
    # treats NULL as DISTINCT, so the new UNIQUE does NOT prevent
    # duplicate (city, target_date, NULL) rows. Subagent-4 adversarial
    # audit (2026-04-24) DEMONSTRATED this on the live DB: two
    # INSERTs with (TESTCITY, '2099-01-01', NULL, 'UNVERIFIED') both
    # succeeded. `scripts/onboard_cities.py:383` is the writer that
    # currently emits NULL-metric scaffold rows.
    #
    # Structural fix: a BEFORE INSERT trigger that rejects NULL metric
    # on ALL rows (not just VERIFIED — the NULL-metric scaffold path
    # bypasses the verified-integrity trigger by inserting as
    # UNVERIFIED). DROP + CREATE for v2 propagation. Live DB has 0 NULL
    # metric rows as of the audit — no existing row rejected.
    try:
        conn.execute("DROP TRIGGER IF EXISTS settlements_non_null_metric")
        conn.execute(
            """
            CREATE TRIGGER settlements_non_null_metric
            BEFORE INSERT ON settlements
            WHEN NEW.temperature_metric IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'settlements.temperature_metric must be non-null (high or low); REOPEN-2 post-audit fix closes the NULL-NULL UNIQUE hole');
            END;
            """
        )
    except sqlite3.OperationalError:
        pass

    # S2.2 (2026-04-23, data-readiness-tail): Structural AP-2 prevention.
    # SettlementSemantics.assert_settlement_value() is a SOCIAL gate (runtime
    # only — any writer that bypasses the function bypasses the check). These
    # two triggers enforce the minimum VERIFIED-row invariants structurally at
    # DB-write time: a row with authority='VERIFIED' must carry non-null
    # settlement_value AND non-empty winning_bin. QUARANTINED rows may have
    # NULL settlement_value (that is the quarantine semantic — row is excluded
    # from the authoritative set until reactivation).
    #
    # Pre-apply probe against live DB (1,469 VERIFIED + 92 QUARANTINED rows):
    #   VERIFIED: 0 with null settlement_value / 0 with null winning_bin → none rejected
    #   QUARANTINED: 49 with null settlement_value / 92 with null winning_bin → trigger does not fire (WHEN gates on authority='VERIFIED')
    # So no legitimate historical rows are rejected by this trigger.
    #
    # DROP + CREATE (not CREATE IF NOT EXISTS) so a future refactor that
    # tightens the predicate propagates to all legacy DBs on next init_schema.
    try:
        conn.execute("DROP TRIGGER IF EXISTS settlements_verified_insert_integrity")
        conn.execute(
            """
            CREATE TRIGGER settlements_verified_insert_integrity
            BEFORE INSERT ON settlements
            WHEN NEW.authority = 'VERIFIED'
              AND (NEW.settlement_value IS NULL
                   OR NEW.winning_bin IS NULL
                   OR NEW.winning_bin = '')
            BEGIN
                SELECT RAISE(ABORT, 'VERIFIED settlement INSERT requires non-null settlement_value + non-empty winning_bin');
            END;
            """
        )
        conn.execute("DROP TRIGGER IF EXISTS settlements_verified_update_integrity")
        conn.execute(
            """
            CREATE TRIGGER settlements_verified_update_integrity
            BEFORE UPDATE OF authority, settlement_value, winning_bin ON settlements
            WHEN NEW.authority = 'VERIFIED'
              AND (NEW.settlement_value IS NULL
                   OR NEW.winning_bin IS NULL
                   OR NEW.winning_bin = '')
            BEGIN
                SELECT RAISE(ABORT, 'VERIFIED settlement UPDATE requires non-null settlement_value + non-empty winning_bin');
            END;
            """
        )
    except sqlite3.OperationalError:
        pass

    # idx_calibration_pairs_decision_group, idx_calibration_pairs_group_lookup,
    # idx_calibration_pairs_group_lookup_lead removed in B3 (PR3):
    # bare calibration_pairs shell dropped; indexes now owned by apply_canonical_schema.
    _ensure_calibration_decision_group_lead_key(conn)

    _ensure_runtime_bootstrap_support_tables(conn)

    # Phase 5A (B069 / SD-1): add temperature_metric to position_current so the
    # portfolio_loader_view can emit per-row metric identity.
    # Zero-Data Golden Window precondition: this ALTER must only run on an empty table.
    try:
        position_current_cols = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
        }
        if "temperature_metric" not in position_current_cols:
            row_count = conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0]
            logger.info(
                "phase5a_alter_position_current: row_count=%d before ADD COLUMN temperature_metric",
                row_count,
            )
            assert row_count == 0, (
                f"Phase 5A ALTER expects empty position_current (Zero-Data Golden Window); "
                f"found {row_count} rows"
            )
            conn.execute(
                "ALTER TABLE position_current ADD COLUMN temperature_metric TEXT NOT NULL DEFAULT 'high' "
                "CHECK (temperature_metric IN ('high', 'low'));"
            )
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise  # Column already exists — idempotent re-run

    # Live monitor quote fields are execution evidence, not belief. Persist
    # them alongside the monitor price so restart/recovery can distinguish a
    # real no-bid exit from a missing quote.
    for _monitor_quote_col in (
        "last_monitor_best_bid",
        "last_monitor_best_ask",
        "last_monitor_market_vig",
    ):
        try:
            conn.execute(f"ALTER TABLE position_current ADD COLUMN {_monitor_quote_col} REAL;")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain-observed economics on
    # position_current. F1 added chain_avg_price / chain_cost_basis_usd to the canonical
    # column contract (src/state/projection.py CANONICAL_POSITION_CURRENT_COLUMNS) and the
    # Position dataclass, but the boot migration was never added — so live trade DBs lack
    # the columns and exchange_reconcile's ordered_values(projection, CANONICAL_...) raises
    # "table position_current has no column named chain_avg_price", breaking the entire
    # exit/PnL reconcile path. Additive nullable REAL — safe on populated tables
    # (idempotent; OperationalError "duplicate column" = already present).
    for _f1_col in ("chain_avg_price", "chain_cost_basis_usd"):
        try:
            conn.execute(f"ALTER TABLE position_current ADD COLUMN {_f1_col} REAL;")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise  # Column already exists — idempotent re-run

    # B091 lower half: add decision_time_status column to selection_family_fact.
    # Additive column — safe on existing DBs (idempotent; OperationalError = already present).
    try:
        conn.execute(
            "ALTER TABLE selection_family_fact ADD COLUMN decision_time_status TEXT;"
        )
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise  # Column already exists — idempotent re-run

    # v1.F20 (2026-05-18): ALTER TABLE ensemble_snapshots ADD COLUMN temperature_metric removed.
    # ensemble_snapshots table dropped; migration no longer applicable.

    # v1.F1 (2026-05-18): db_chunk_boundary_events DDL registration.
    # The table is declared world_class in architecture/db_table_ownership.yaml
    # but its DDL lived only in src/state/chunk_boundary_events.ensure_table().
    # Adding here ensures init_schema creates it so assert_db_matches_registry
    # (INV-05 boot gate) passes on a fresh DB.  Delegate to the schema owner
    # to avoid DDL duplication — same pattern as SETTLEMENT_COMMAND_SCHEMA above.
    from src.state.chunk_boundary_events import ensure_table as _ensure_chunk_boundary_table
    _ensure_chunk_boundary_table(conn)

    # Phase 2 T1 (2026-05-20): book_hash_transitions table + indices.
    from src.state.schema.book_hash_transitions_schema import ensure_table as _ensure_book_hash_transitions_table
    _ensure_book_hash_transitions_table(conn)

    # Phase 2 T2 (2026-05-20): no_trade_events table + indices (SCHEMA_VERSION 15).
    from src.state.schema.no_trade_events_schema import migrate_no_trade_events_schema as _migrate_no_trade_events_schema
    _migrate_no_trade_events_schema(conn)

    # EDLI v1 (2026-05-24): append-only opportunity event store tables.
    from src.state.schema.opportunity_events_schema import ensure_table as _ensure_opportunity_events_table
    from src.state.schema.opportunity_event_processing_schema import ensure_table as _ensure_opportunity_event_processing_table
    from src.state.schema.event_dead_letters_schema import ensure_table as _ensure_event_dead_letters_table
    _ensure_opportunity_events_table(conn)
    _ensure_opportunity_event_processing_table(conn)
    _ensure_event_dead_letters_table(conn)

    # EDLI v1 (2026-05-24): event-triggered no-trade regret ledger.
    from src.state.schema.no_trade_regret_events_schema import ensure_table as _ensure_no_trade_regret_events_table
    _ensure_no_trade_regret_events_table(conn)

    # EDLI v1 (2026-05-24): durable accepted no-submit receipt ledger.
    from src.state.schema.edli_no_submit_receipts_schema import ensure_table as _ensure_edli_no_submit_receipts_table
    _ensure_edli_no_submit_receipts_table(conn)

    # EDLI v1 (2026-05-24): durable tiny live-cap usage ledger.
    from src.state.schema.edli_live_cap_usage_schema import ensure_table as _ensure_edli_live_cap_usage_table
    _ensure_edli_live_cap_usage_table(conn)

    # EDLI full-live split (2026-05-25): live-order aggregate event log + projection.
    from src.state.schema.edli_live_order_events_schema import ensure_tables as _ensure_edli_live_order_events_tables
    _ensure_edli_live_order_events_tables(conn)

    # EDLI live promotion (2026-05-26): event-bound realized-edge audit projection.
    from src.state.schema.edli_live_profit_audit_schema import ensure_table as _ensure_edli_live_profit_audit_table
    _ensure_edli_live_profit_audit_table(conn)

    # Settlement skill-attribution (2026-06-12): per-settled-position skill-vs-luck
    # grade ledger (SKILL_WIN / LUCKY_WIN / SKILL_LOSS / MISCALIBRATED_LOSS /
    # STALE_DECISION). Sole writer = src/analysis/settlement_skill_attribution.py.
    from src.state.schema.settlement_attribution_schema import ensure_table as _ensure_settlement_attribution_table
    _ensure_settlement_attribution_table(conn)

    # Exit-timing grade ledger (2026-06-22, lifecycle consult): the orthogonal
    # exit-decision attribution axis (exit_alpha vs counterfactual hold). Sole
    # writer = src/analysis/exit_timing_attribution.py.
    from src.state.schema.exit_timing_attribution_schema import ensure_table as _ensure_exit_timing_attribution_table
    _ensure_exit_timing_attribution_table(conn)

    # Family-rebalance lifecycle lease (2026-06-22, lifecycle consult): the
    # concurrency guard (one active rebalance per family) for D1 fill-up / D2
    # shift-bin. Sole writer = src/strategy/family_rebalance.py (lease manager).
    from src.state.schema.family_rebalance_intents_schema import ensure_table as _ensure_family_rebalance_intents_table
    _ensure_family_rebalance_intents_table(conn)

    # EDLI redemption (2026-05-25): proof-carrying decision certificate ledger.
    from src.state.schema.decision_certificates_schema import ensure_tables as _ensure_decision_certificate_tables
    _ensure_decision_certificate_tables(conn)

    # fill-bridge retry-spiral fix (2026-06-12): per-aggregate terminal disposition table.
    # Prevents settled-market infinite retry and quarantines persistently-failing aggregates.
    from src.state.schema.edli_fill_bridge_dispositions_schema import ensure_table as _ensure_edli_fill_bridge_dispositions_table
    _ensure_edli_fill_bridge_dispositions_table(conn)

    # 2026-05-21 live authority follow-up: decision_events CHECK constraints
    # must admit offline_decision / unknown_legacy. CREATE TABLE IF NOT EXISTS
    # cannot upgrade stale CHECKs.
    _migrate_decision_events_schema(conn)
    _migrate_world_strategy_key_checks(conn)
    _migrate_market_scan_authority_checks(conn)
    _migrate_readiness_state_status_checks(conn)

    # Phase 3 T3 (2026-05-21): shoulder_exposure_ledger table (SCHEMA_VERSION 23).
    from src.state.schema.shoulder_exposure_ledger_schema import ensure_table as _ensure_shoulder_exposure_ledger_table
    _ensure_shoulder_exposure_ledger_table(conn)

    # Phase 5 T2 (2026-05-21): regime_correlation_cache table (SCHEMA_VERSION 24).
    from src.state.schema.regime_correlation_cache_schema import ensure_table as _ensure_regime_correlation_cache_table
    _ensure_regime_correlation_cache_table(conn)

    # Phase 6 T2+T3 (2026-05-21): evidence_tier_assignments and
    # regret_decompositions tables (SCHEMA_VERSION 25/26).
    from src.state.schema.phase6_evidence_schema import ensure_tables as _ensure_phase6_evidence_tables
    _ensure_phase6_evidence_tables(conn)

    # Phase 2: apply v2 schema (idempotent — safe to run on every boot).
    from src.state.schema.v2_schema import apply_canonical_schema as _apply_canonical_schema
    _apply_canonical_schema(conn, forecast_tables=False)

    # Zeus #64 FT-ship F2 (2026-05-26): ensure model_bias_ens exists on every
    # init_schema target so monitor_refresh + evaluator can read FT models at runtime
    # without crashing on "no such table". Idempotent CREATE TABLE IF NOT EXISTS.
    # Authority: docs/archive/2026-Q2/operations_historical/FT_SHIP_EXECUTION_LEDGER_2026-05-25.md F2.
    from src.calibration.ens_bias_repo import init_ens_bias_schema as _init_ens_bias_schema
    _init_ens_bias_schema(conn)

    # db_chunk_boundary_events — K2 live-contention event log (Cluster B fix 2026-05-18)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS db_chunk_boundary_events (
            event_id       TEXT PRIMARY KEY,
            occurred_at    TEXT NOT NULL,
            caller_module  TEXT NOT NULL,
            db_path        TEXT NOT NULL,
            rows_processed INTEGER NOT NULL DEFAULT 0,
            duration_ms    INTEGER NOT NULL DEFAULT 0,
            split_reason   TEXT NOT NULL
                CHECK (split_reason IN ('LIVE_CONTENDED', 'WATCHDOG', 'MANUAL'))
        )
    """)

    if own_conn:
        conn.commit()
        conn.close()


def _migrate_decision_events_schema(conn: sqlite3.Connection) -> None:
    """Upgrade stale decision_events CHECK constraints without losing rows."""

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='decision_events'"
    ).fetchone()
    table_sql = str(row[0] if row else "")
    if not table_sql:
        return
    if (
        "unknown_legacy" in table_sql
        and "offline_decision" in table_sql
        and "12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28" in table_sql
    ):
        conn.execute("DROP TABLE IF EXISTS decision_events_new")
        return

    conn.execute("DROP TABLE IF EXISTS decision_events_new")
    conn.execute(
        """
        CREATE TABLE decision_events_new (
            market_slug         TEXT NOT NULL,
            temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
            target_date         TEXT NOT NULL,
            observation_time    TEXT NOT NULL,
            decision_seq        INTEGER NOT NULL,
            condition_id        TEXT,
            decision_event_id   TEXT,
            decision_time       TEXT NOT NULL,
            outcome             TEXT NOT NULL,
            side                TEXT NOT NULL,
            strategy_key        TEXT NOT NULL,
            cycle_id            TEXT,
            cycle_iteration     INTEGER,
            p_posterior         REAL,
            edge                REAL,
            target_size_usd     REAL,
            target_price        REAL,
            forecast_time              TEXT,
            provider_reported_time     TEXT,
            observation_available_at   TEXT NOT NULL,
            polymarket_end_anchor_source TEXT NOT NULL CHECK (
                polymarket_end_anchor_source IN ('gamma_explicit', 'f1_12z_fallback', 'unknown_legacy')
            ),
            first_member_observed_time TEXT,
            run_complete_time          TEXT,
            zeus_submit_intent_time    TEXT,
            venue_ack_time             TEXT,
            first_inclusion_block_time TEXT,
            finality_confirmed_time    TEXT,
            clock_skew_estimate_ms_at_submit INTEGER,
            raw_orderbook_hash_transition_delta_ms INTEGER,
            schema_version INTEGER NOT NULL CHECK (schema_version IN (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)),
            source         TEXT NOT NULL CHECK (source IN ('phase0_backfill', 'live_decision', 'offline_decision')),
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO decision_events_new (
            market_slug, temperature_metric, target_date, observation_time,
            decision_seq, condition_id, decision_event_id, decision_time,
            outcome, side, strategy_key, cycle_id, cycle_iteration,
            p_posterior, edge, target_size_usd, target_price,
            forecast_time, provider_reported_time, observation_available_at,
            polymarket_end_anchor_source, first_member_observed_time,
            run_complete_time, zeus_submit_intent_time, venue_ack_time,
            first_inclusion_block_time, finality_confirmed_time,
            clock_skew_estimate_ms_at_submit, raw_orderbook_hash_transition_delta_ms,
            schema_version, source
        )
        SELECT
            market_slug, temperature_metric, target_date, observation_time,
            decision_seq, condition_id, decision_event_id, decision_time,
            outcome, side, strategy_key, cycle_id, cycle_iteration,
            p_posterior, edge, target_size_usd, target_price,
            forecast_time, provider_reported_time, observation_available_at,
            CASE
                WHEN polymarket_end_anchor_source IN ('gamma_explicit', 'f1_12z_fallback', 'unknown_legacy')
                    THEN polymarket_end_anchor_source
                ELSE 'unknown_legacy'
            END,
            first_member_observed_time, run_complete_time,
            zeus_submit_intent_time, venue_ack_time,
            first_inclusion_block_time, finality_confirmed_time,
            clock_skew_estimate_ms_at_submit, raw_orderbook_hash_transition_delta_ms,
            CASE
                WHEN schema_version IN (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)
                    THEN schema_version
                ELSE 36
            END,
            CASE
                WHEN source IN ('phase0_backfill', 'live_decision', 'offline_decision')
                    THEN source
                ELSE 'phase0_backfill'
            END
        FROM decision_events
        """
    )
    conn.execute("DROP TABLE decision_events")
    conn.execute("ALTER TABLE decision_events_new RENAME TO decision_events")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_events_slug_date "
        "ON decision_events(market_slug, target_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_events_strategy "
        "ON decision_events(strategy_key, decision_time)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_events_event_id "
        "ON decision_events(decision_event_id)"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS decision_events_event_id_backstop
        AFTER INSERT ON decision_events
        FOR EACH ROW
        WHEN NEW.decision_event_id IS NULL
        BEGIN
            UPDATE decision_events
               SET decision_event_id = 'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'
             WHERE market_slug = NEW.market_slug
               AND temperature_metric = NEW.temperature_metric
               AND target_date = NEW.target_date
               AND observation_time = NEW.observation_time
               AND decision_seq = NEW.decision_seq;
        END
        """
    )


class SchemaOutOfDateError(RuntimeError):
    """Raised when the DB schema does not meet structural readiness requirements."""


class BridgeAbsentError(RuntimeError):
    """position_current row has no matching trade_decisions.runtime_trade_id bridge.

    Raised by update_trade_lifecycle when the synthesizer cannot reconstruct
    the missing bridge row from available join tables.  Signals a real cascade
    defect that requires operator investigation.
    """


def assert_schema_current(conn: sqlite3.Connection) -> None:
    """B2 (2026-05-28): SCHEMA_VERSION counter cancelled; this check is now a no-op.
    Schema drift is detected via content-hash fingerprint (scripts/check_schema_fingerprint.py).
    Retained for call-site compatibility."""


# ---------------------------------------------------------------------------
# K1 forecast DB split — 2026-05-11
# ---------------------------------------------------------------------------
# B2 (2026-05-28): SCHEMA_FORECASTS_VERSION counter cancelled alongside SCHEMA_VERSION.
# Forecast schema drift is detected via content-hash fingerprint.


# B3cont (2026-05-28): _create_settlements removed — bare world-class settlements shell dropped.
# Canonical table is settlement_outcomes in zeus-forecasts.db (_create_settlement_outcomes).


def _create_observations(conn: sqlite3.Connection) -> None:
    """Create observations table + index. Idempotent. K1 forecast-class table."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            high_temp REAL,
            low_temp REAL,
            unit TEXT NOT NULL,
            station_id TEXT,
            fetched_at TEXT,
            high_raw_value REAL,
            high_raw_unit TEXT CHECK (high_raw_unit IN ('F', 'C', 'K')),
            high_target_unit TEXT CHECK (high_target_unit IN ('F', 'C')),
            low_raw_value REAL,
            low_raw_unit TEXT CHECK (low_raw_unit IN ('F', 'C', 'K')),
            low_target_unit TEXT CHECK (low_target_unit IN ('F', 'C')),
            high_fetch_utc TEXT,
            high_local_time TEXT,
            high_collection_window_start_utc TEXT,
            high_collection_window_end_utc TEXT,
            low_fetch_utc TEXT,
            low_local_time TEXT,
            low_collection_window_start_utc TEXT,
            low_collection_window_end_utc TEXT,
            timezone TEXT,
            utc_offset_minutes INTEGER,
            dst_active INTEGER CHECK (dst_active IN (0, 1)),
            is_ambiguous_local_hour INTEGER CHECK (is_ambiguous_local_hour IN (0, 1)),
            is_missing_local_hour INTEGER CHECK (is_missing_local_hour IN (0, 1)),
            hemisphere TEXT CHECK (hemisphere IN ('N', 'S')),
            season TEXT CHECK (season IN ('DJF', 'MAM', 'JJA', 'SON')),
            month INTEGER CHECK (month BETWEEN 1 AND 12),
            rebuild_run_id TEXT,
            data_source_version TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            high_provenance_metadata TEXT,
            low_provenance_metadata TEXT,
            UNIQUE(city, target_date, source)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_observations_city_date
            ON observations(city, target_date, source)
    """)


def _create_source_run(conn: sqlite3.Connection) -> None:
    """Create source_run table + indexes. Idempotent. K1 forecast-class table."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            track TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            ingest_mode TEXT NOT NULL CHECK (ingest_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            origin_mode TEXT NOT NULL CHECK (origin_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            source_cycle_time TEXT NOT NULL,
            source_issue_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            fetch_started_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            imported_at TEXT,
            valid_time_start TEXT,
            valid_time_end TEXT,
            target_local_date TEXT,
            city_id TEXT,
            city_timezone TEXT,
            temperature_metric TEXT
                CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            dataset_id TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            expected_steps_json TEXT NOT NULL DEFAULT '[]',
            observed_steps_json TEXT NOT NULL DEFAULT '[]',
            expected_count INTEGER,
            observed_count INTEGER,
            completeness_status TEXT NOT NULL CHECK (completeness_status IN (
                'COMPLETE','PARTIAL','MISSING','NOT_RELEASED'
            )),
            partial_run INTEGER NOT NULL DEFAULT 0 CHECK (partial_run IN (0,1)),
            raw_payload_hash TEXT,
            manifest_hash TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED'
            )),
            reason_code TEXT,
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            CHECK (partial_run = 0 OR completeness_status = 'PARTIAL')
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_run_source_cycle
            ON source_run(source_id, track, source_cycle_time)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_run_scope
            ON source_run(city_id, city_timezone, target_local_date,
                          temperature_metric, dataset_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_run_status
            ON source_run(status, completeness_status, source_cycle_time)
    """)


def _create_job_run(conn: sqlite3.Connection) -> None:
    """Create job_run table + indexes. Idempotent forecast-live work journal."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_run (
            job_run_id TEXT PRIMARY KEY,
            job_run_key TEXT NOT NULL UNIQUE,
            job_name TEXT NOT NULL,
            plane TEXT NOT NULL CHECK (plane IN (
                'forecast','observation','solar_aux','market_topology',
                'quote','settlement_truth','source_health','hole_backfill','telemetry_control'
            )),
            scheduled_for TEXT NOT NULL,
            missed_from TEXT,
            started_at TEXT,
            finished_at TEXT,
            lock_key TEXT,
            lock_acquired_at TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED','SKIPPED_LOCK_HELD'
            )),
            reason_code TEXT,
            rows_written INTEGER NOT NULL DEFAULT 0,
            rows_failed INTEGER NOT NULL DEFAULT 0,
            source_run_id TEXT,
            source_id TEXT,
            track TEXT,
            release_calendar_key TEXT,
            safe_fetch_not_before TEXT,
            expected_scope_json TEXT NOT NULL DEFAULT '{}',
            affected_scope_json TEXT NOT NULL DEFAULT '{}',
            readiness_impacts_json TEXT NOT NULL DEFAULT '[]',
            readiness_recomputed_at TEXT,
            meta_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(job_name, scheduled_for, source_id, track, release_calendar_key)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_run_job_window
            ON job_run(job_name, scheduled_for)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_run_plane_status
            ON job_run(plane, status, scheduled_for)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_run_source_run
            ON job_run(source_run_id)
    """)


def _job_run_release_key_identity_current(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='job_run'"
    ).fetchone()
    if not row or not row[0]:
        return False
    normalized = " ".join(str(row[0]).replace("\n", " ").split())
    return "UNIQUE(job_name, scheduled_for, source_id, track, release_calendar_key)" in normalized


def _ensure_job_run_release_key_identity(conn: sqlite3.Connection) -> None:
    """Rebuild legacy job_run UNIQUE scope to include release_calendar_key."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_run'"
    ).fetchone()
    if not exists or _job_run_release_key_identity_current(conn):
        return

    conn.execute("DROP TABLE IF EXISTS job_run_release_key_rebuild")
    conn.execute("""
        CREATE TABLE job_run_release_key_rebuild (
            job_run_id TEXT PRIMARY KEY,
            job_run_key TEXT NOT NULL UNIQUE,
            job_name TEXT NOT NULL,
            plane TEXT NOT NULL CHECK (plane IN (
                'forecast','observation','solar_aux','market_topology',
                'quote','settlement_truth','source_health','hole_backfill','telemetry_control'
            )),
            scheduled_for TEXT NOT NULL,
            missed_from TEXT,
            started_at TEXT,
            finished_at TEXT,
            lock_key TEXT,
            lock_acquired_at TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED','SKIPPED_LOCK_HELD'
            )),
            reason_code TEXT,
            rows_written INTEGER NOT NULL DEFAULT 0,
            rows_failed INTEGER NOT NULL DEFAULT 0,
            source_run_id TEXT,
            source_id TEXT,
            track TEXT,
            release_calendar_key TEXT,
            safe_fetch_not_before TEXT,
            expected_scope_json TEXT NOT NULL DEFAULT '{}',
            affected_scope_json TEXT NOT NULL DEFAULT '{}',
            readiness_impacts_json TEXT NOT NULL DEFAULT '[]',
            readiness_recomputed_at TEXT,
            meta_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(job_name, scheduled_for, source_id, track, release_calendar_key)
        )
    """)
    conn.execute("""
        INSERT OR REPLACE INTO job_run_release_key_rebuild (
            job_run_id, job_run_key, job_name, plane, scheduled_for, missed_from,
            started_at, finished_at, lock_key, lock_acquired_at, status,
            reason_code, rows_written, rows_failed, source_run_id,
            source_id, track, release_calendar_key, safe_fetch_not_before,
            expected_scope_json, affected_scope_json, readiness_impacts_json,
            readiness_recomputed_at, meta_json, recorded_at
        )
        SELECT
            job_run_id,
            job_name || '|' || scheduled_for || '|' ||
                COALESCE(source_id, '') || '|' || COALESCE(track, '') || '|' ||
                COALESCE(release_calendar_key, ''),
            job_name, plane, scheduled_for, missed_from,
            started_at, finished_at, lock_key, lock_acquired_at, status,
            reason_code, rows_written, rows_failed, source_run_id,
            source_id, track, release_calendar_key, safe_fetch_not_before,
            expected_scope_json, affected_scope_json, readiness_impacts_json,
            readiness_recomputed_at, meta_json, recorded_at
        FROM job_run
    """)
    conn.execute("DROP TABLE job_run")
    conn.execute("ALTER TABLE job_run_release_key_rebuild RENAME TO job_run")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_run_job_window
            ON job_run(job_name, scheduled_for)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_run_plane_status
            ON job_run(plane, status, scheduled_for)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_run_source_run
            ON job_run(source_run_id)
    """)


def _create_source_run_coverage(conn: sqlite3.Connection) -> None:
    """Create source_run_coverage table + indexes. Idempotent forecast authority table."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_transport TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            city_id TEXT NOT NULL,
            city TEXT NOT NULL,
            city_timezone TEXT NOT NULL,
            target_local_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
            physical_quantity TEXT NOT NULL,
            observation_field TEXT NOT NULL,
            data_version TEXT NOT NULL,
            expected_members INTEGER NOT NULL,
            observed_members INTEGER NOT NULL,
            expected_steps_json TEXT NOT NULL,
            observed_steps_json TEXT NOT NULL,
            snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            target_window_start_utc TEXT NOT NULL,
            target_window_end_utc TEXT NOT NULL,
            completeness_status TEXT NOT NULL CHECK (completeness_status IN (
                'COMPLETE','PARTIAL','MISSING','HORIZON_OUT_OF_RANGE','NOT_RELEASED'
            )),
            readiness_status TEXT NOT NULL CHECK (readiness_status IN (
                'LIVE_ELIGIBLE','BLOCKED','UNKNOWN_BLOCKED'
            )),
            reason_code TEXT,
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(
                source_run_id, source_id, source_transport, release_calendar_key,
                track, city_id, city_timezone, target_local_date,
                temperature_metric, data_version
            )
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_run_coverage_scope
            ON source_run_coverage(city_id, city_timezone, target_local_date,
                                   temperature_metric, source_id,
                                   source_transport, data_version)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_run_coverage_status
            ON source_run_coverage(readiness_status, completeness_status, computed_at)
    """)


def _create_readiness_state(conn: sqlite3.Connection) -> None:
    """Create readiness_state table + indexes. Idempotent forecast authority table."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readiness_state (
            readiness_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            scope_type TEXT NOT NULL CHECK (scope_type IN (
                'global','source','city_metric','market','strategy','quote'
            )),
            city_id TEXT,
            city TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            metric TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            source_id TEXT,
            track TEXT,
            source_run_id TEXT,
            market_family TEXT,
            event_id TEXT,
            condition_id TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            strategy_key TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'READY','LIVE_ELIGIBLE','BLOCKED','UNKNOWN_BLOCKED'
            )),
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            dependency_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(
                scope_type, city_id, city_timezone, target_local_date,
                temperature_metric, physical_quantity, observation_field,
                data_version, strategy_key, market_family, source_id, track,
                condition_id
            )
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_readiness_state_entry_scope
            ON readiness_state(city_id, city_timezone, target_local_date,
                               temperature_metric, strategy_key,
                               market_family, condition_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_readiness_state_status_expiry
            ON readiness_state(status, expires_at)
    """)


_FORECAST_TABLES = (
    "ensemble_snapshots",
    "source_run",
    "job_run",
    "source_run_coverage",
    "readiness_state",
    "observations",
    # B3cont (2026-05-28): bare "settlements" world-class shell dropped; removed from forecast tuple
    "calibration_pairs",
    "settlement_outcomes",
    "market_events",
    # T2 Day0Nowcast — SCHEMA_FORECASTS_VERSION 4 (2026-05-19)
    "day0_horizon_platt_fits",
    "day0_nowcast_runs",
    # T4 MarketAnalysisVNext — SCHEMA_FORECASTS_VERSION 5 (2026-05-21)
    "market_microstructure_snapshots",
    # Phase 7 T3 — SCHEMA_FORECASTS_VERSION 6 (2026-05-21)
    "settlement_capture_verifications",
    # Data Temporal Kernel — SCHEMA_FORECASTS_VERSION 7 PR #329 D (2026-05-24)
    "source_time_frontier",
    # Replacement forecast live-authority provenance (2026-06-07).
    "raw_forecast_artifacts",
    "deterministic_forecast_anchors",
    "forecast_posteriors",
    # 2026-07-01 DB-ownership cleanup: sync this legacy constant with the registry
    # forecast_class set (closes test_a1 drift). NOTE: init_schema_forecasts derives its
    # table list from tables_for_class(FORECAST_CLASS), NOT this constant (P2 refactor
    # 2026-05-14), so this tuple is a coherence witness only — no runtime driver. These 4
    # were already forecast_class in the registry but absent here. settlements stays OUT
    # (B3cont 2026-05-28 dropped its forecasts shell; reclassified legacy_archived here).
    "raw_model_forecasts",
    "raw_model_forecast_request_conflicts",
    "cycle_advance_enqueues",
    "fusion_upgrade_enqueues",
)


def _ensure_forecast_indexes(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE INDEX IF NOT EXISTS for every v2 forecast-class index.

    PLAN-evidence: docs/operations/task_2026-05-14_attach_path_index_fix/PLAN.md
    Option A (2026-05-14). Bug category being closed: the ATTACH-from-world.db
    path inside init_schema_forecasts copies indexes from world_src.sqlite_master,
    but world.db may trail v2_schema.py (partial migration, init_schema_world_only
    deploy, test fixture). Calling this helper unconditionally after
    init_schema_forecasts's table-creation branches guarantees post-condition
    equivalence between the ATTACH path and the static-fallback path.

    Canonical truth source for the index list is
    src/state/schema/v2_schema.py (the four _create_*_v2 helpers). DDL text
    here must match those helpers byte-for-byte. If a new v2 forecast-class
    index is added to v2_schema.py, mirror it here in the same PR.

    Tables covered: ensemble_snapshots, calibration_pairs,
    settlement_outcomes, market_events.
    """
    # settlement_outcomes (mirror src/state/schema/v2_schema.py — _create_settlement_outcomes)
    # B3cont (2026-05-28): renamed from settlement_outcomes.
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_settlement_outcomes_city_date_metric
            ON settlement_outcomes(city, target_date, temperature_metric)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_settlement_outcomes_settled_at
            ON settlement_outcomes(settled_at)
    """)
    # market_events (mirror src/state/schema/v2_schema.py:80-89)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_events_city_date_metric
            ON market_events(city, target_date, temperature_metric)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_events_condition_id
            ON market_events(condition_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_events_open
            ON market_events(city, target_date, temperature_metric)
            WHERE outcome IS NULL
    """)
    # ensemble_snapshots (mirror src/state/schema/v2_schema.py:166-229)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ensemble_snapshots_lookup
            ON ensemble_snapshots(city, target_date, temperature_metric, available_at)
    """)
    conn.execute("DROP INDEX IF EXISTS idx_ens_v2_source_run")
    conn.execute("DROP INDEX IF EXISTS idx_ens_v2_entry_lookup")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ens_source_run
            ON ensemble_snapshots(source_id, source_transport, source_run_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ens_entry_lookup
            ON ensemble_snapshots(
                city,
                target_date,
                temperature_metric,
                source_id,
                source_transport,
                dataset_id,
                source_run_id
            )
    """)
    # calibration_pairs (mirror src/state/schema/v2_schema.py — _create_calibration_pairs)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_pairs_bucket
            ON calibration_pairs(temperature_metric, cluster, season, lead_days)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_pairs_city_date_metric
            ON calibration_pairs(city, target_date, temperature_metric)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_pairs_refit_core
            ON calibration_pairs(temperature_metric, dataset_id, training_allowed, authority)
    """)


def ensure_forecast_runtime_indexes(conn: sqlite3.Connection) -> None:
    """Idempotently converge hot forecast-class indexes needed by live readers."""

    _ensure_forecast_indexes(conn)


def _create_day0_horizon_platt_fits(conn: sqlite3.Connection) -> None:
    """Create day0_horizon_platt_fits table. Idempotent. K1 forecast-class.

    One row per HorizonPlattFit execution (fit_run_id = uuid4 PK).
    Referenced as FK by day0_nowcast_runs.fit_run_id.

    T2 Day0Nowcast — SCHEMA_FORECASTS_VERSION 4 (2026-05-19).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS day0_horizon_platt_fits (
            fit_run_id          TEXT PRIMARY KEY,
            fit_version         TEXT NOT NULL,
            alpha               REAL NOT NULL,
            beta                REAL NOT NULL,
            gamma_morning       REAL NOT NULL,
            gamma_afternoon     REAL NOT NULL,
            gamma_post_peak     REAL NOT NULL,
            delta               REAL NOT NULL,
            epsilon             REAL NOT NULL,
            fit_date            TEXT,
            n_obs               INTEGER NOT NULL,
            sample_period_start TEXT,
            sample_period_end   TEXT,
            schema_version      INTEGER NOT NULL CHECK (schema_version IN (3, 4, 5)),
            source              TEXT NOT NULL CHECK (source IN ('live_fit', 'replay_fit'))
        )
    """)


def _create_day0_nowcast_runs(conn: sqlite3.Connection) -> None:
    """Create day0_nowcast_runs table + AFTER INSERT trigger + indexes. Idempotent.

    K1 forecast-class table. One row per Day0HighNowcastSignal evaluation.
    nowcast_event_id (nei_v1_ namespace) computed writer-side; AFTER INSERT
    trigger is a backstop sentinel for NULL writer-bypass.

    T2 Day0Nowcast — SCHEMA_FORECASTS_VERSION 4 (2026-05-19).
    bin_grid_id deferred to Phase 2 (no propagation path at Day0 caller sites).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS day0_nowcast_runs (
            market_slug         TEXT NOT NULL,
            temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
            target_date         TEXT NOT NULL,
            observation_time    TEXT NOT NULL,
            run_seq             INTEGER NOT NULL,
            nowcast_event_id    TEXT,
            fit_run_id          TEXT NOT NULL
                REFERENCES day0_horizon_platt_fits(fit_run_id),
            p_nowcast_json      TEXT,
            p_now_raw_json      TEXT,
            hours_remaining     REAL NOT NULL,
            daypart             TEXT NOT NULL
                CHECK (daypart IN ('pre_sunrise','morning','afternoon','post_peak')),
            schema_version      INTEGER NOT NULL CHECK (schema_version IN (3, 4, 5, 7)),
            source              TEXT NOT NULL CHECK (source IN ('live_nowcast', 'replay')),
            -- ThePath P1 ITEM 1 (2026-06-07): forward-only obs-availability instrumentation.
            -- observation_available_at = wall-clock time Zeus could query the obs that fed
            -- this run (Day0ObservationContext.observation_available_at = now()-at-fetch).
            -- NEVER synthesized from now() in the writer; absent => NULL + 'UNVERIFIED'.
            -- obs_availability_provenance enumerated; CHECK permits NULL for legacy rows.
            observation_available_at    TEXT,
            obs_availability_provenance TEXT
                CHECK (obs_availability_provenance IS NULL OR obs_availability_provenance IN
                    ('live_fetch','rolling_hourly_imported_at','archive_dissemination_lag','UNVERIFIED')),
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, run_seq)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_day0_nowcast_runs_slug_date
            ON day0_nowcast_runs(market_slug, target_date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_day0_nowcast_runs_event_id
            ON day0_nowcast_runs(nowcast_event_id)
    """)
    # AFTER INSERT backstop: if writer failed to supply nowcast_event_id,
    # stamp sentinel so NULL rows are detectable in audit queries.
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_day0_nowcast_runs_nei_backstop
        AFTER INSERT ON day0_nowcast_runs
        WHEN NEW.nowcast_event_id IS NULL
        BEGIN
            UPDATE day0_nowcast_runs
            SET nowcast_event_id = 'nei_v1_BACKSTOP_NULL_WRITER_BYPASS'
            WHERE market_slug        = NEW.market_slug
              AND temperature_metric = NEW.temperature_metric
              AND target_date        = NEW.target_date
              AND observation_time   = NEW.observation_time
              AND run_seq            = NEW.run_seq;
        END
    """)


def _create_market_microstructure_snapshots(conn: sqlite3.Connection) -> None:
    """Create market_microstructure_snapshots table. Idempotent.

    K1 forecast-class only table; not in world_src so always created via this
    static helper (ATTACH branch will not find it in world_src.sqlite_master).

    T4 MarketAnalysisVNext — SCHEMA_FORECASTS_VERSION 5 (2026-05-21).
    One row per MarketAnalysisVNext.compute() result.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_microstructure_snapshots (
            id                              INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id                     TEXT NOT NULL UNIQUE,
            event_slug                      TEXT NOT NULL,
            condition_id                    TEXT NOT NULL,
            captured_at_iso                 TEXT NOT NULL,
            wide_spread_display_substitution INTEGER NOT NULL CHECK (wide_spread_display_substitution IN (0, 1)),
            spread_observed_window_ms       INTEGER,
            depth_at_best_ask               INTEGER NOT NULL DEFAULT 0,
            polymarket_end_anchor_source    TEXT NOT NULL DEFAULT 'unknown_legacy',
            bin_grid_id                     TEXT,
            bin_schema_id              TEXT,
            schema_version                  INTEGER NOT NULL DEFAULT 5
                CHECK (schema_version IN (5)),
            recorded_at                     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mms_snapshot_id
            ON market_microstructure_snapshots(snapshot_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mms_event_slug_captured
            ON market_microstructure_snapshots(event_slug, captured_at_iso)
    """)



def _strip_strategy_key_check(sql: str) -> str:
    """Remove strategy_key inline CHECK from a CREATE TABLE DDL string."""
    import re as _re
    return _re.sub(
        r"(strategy_key\s+TEXT(?:\s+NOT\s+NULL)?)\s+CHECK\s*\(strategy_key\s+IN\s*\([^)]+\)\)",
        r"\1",
        sql,
    )


def _legacy_alter_table_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("PRAGMA legacy_alter_table").fetchone()
    return bool(row and int(row[0] or 0))


def _set_legacy_alter_table(conn: sqlite3.Connection, enabled: bool) -> None:
    conn.execute(f"PRAGMA legacy_alter_table = {'ON' if enabled else 'OFF'}")


def _migrate_world_strategy_key_checks(conn: sqlite3.Connection) -> None:
    """Remove stale hardcoded strategy_key CHECK from telemetry tables.

    Finding 6 (P2, 2026-05-22): probability_trace_fact and strategy_health had
    a hardcoded CHECK enumerating the 4 founding strategies. Day0_nowcast_entry
    and future registry additions would fail with CHECK constraint violations.
    Application-layer _strategy_key_for() + registry are the authoritative enum.
    Uses full table-swap so existing rows are preserved.
    """
    import re as _re  # noqa: F811
    for tname in ("probability_trace_fact", "strategy_health", "opportunity_fact"):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tname,)
        ).fetchone()
        if row is None:
            continue
        old_sql = str(row[0])
        if "'opening_inertia'" not in old_sql or "day0_nowcast_entry" in old_sql:
            continue  # Already migrated or no stale CHECK
        indexes = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (tname,),
        ).fetchall()
        new_create = _strip_strategy_key_check(old_sql)
        new_create = _re.sub(
            rf"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+{_re.escape(tname)}\b",
            f"CREATE TABLE {tname}_new",
            new_create,
        )
        conn.execute(f"DROP TABLE IF EXISTS {tname}_new")
        conn.execute(new_create)
        conn.execute(f"INSERT INTO {tname}_new SELECT * FROM {tname}")
        conn.execute(f"DROP TABLE {tname}")
        _legacy_alter_was_enabled = _legacy_alter_table_enabled(conn)
        _set_legacy_alter_table(conn, True)
        try:
            conn.execute(f"ALTER TABLE {tname}_new RENAME TO {tname}")
        finally:
            _set_legacy_alter_table(conn, _legacy_alter_was_enabled)
        for (idx_sql,) in indexes:
            conn.execute(idx_sql)


def _migrate_market_scan_authority_checks(conn: sqlite3.Connection) -> None:
    """Replace ambiguous EMPTY_FALLBACK scan-authority CHECK values.

    EMPTY_FALLBACK conflated two different runtime facts: network fetch failed
    without cache, and keyword-discovery recovery with weaker provenance. The
    new CHECK admits precise facts only. Existing rows with the old value are
    intentionally not auto-mapped because their original cause is unrecoverable.
    """

    _migrate_market_topology_state_authority_checks(conn)
    _migrate_source_contract_audit_authority_checks(conn)


def _migrate_readiness_state_status_checks(conn: sqlite3.Connection) -> None:
    """Remove log-only readiness from the executable readiness vocabulary.

    ``DEGRADED_LOG_ONLY`` was an observe-only/status-reporting word admitted by
    the live readiness table. A readiness row either authorizes the live path
    (``LIVE_ELIGIBLE``) or blocks it with evidence. If old rows still exist, the
    migration fails instead of silently relabeling them.
    """

    sql = _table_create_sql(conn, "readiness_state")
    if not sql or "DEGRADED_LOG_ONLY" not in sql:
        return
    stale_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
          FROM readiness_state
         WHERE status NOT IN ('READY','LIVE_ELIGIBLE','BLOCKED','UNKNOWN_BLOCKED')
         GROUP BY status
         ORDER BY status
        """
    ).fetchall()
    if stale_rows:
        details = ", ".join(f"{row[0]}={row[1]}" for row in stale_rows)
        raise RuntimeError(
            "readiness_state contains obsolete non-live status row(s); "
            f"classify them before schema upgrade: {details}"
        )
    before = conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0]
    conn.execute("DROP TABLE IF EXISTS readiness_state_status_migrated")
    conn.execute(
        """
        CREATE TABLE readiness_state_status_migrated (
            readiness_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            scope_type TEXT NOT NULL CHECK (scope_type IN (
                'global','source','city_metric','market','strategy','quote'
            )),
            city_id TEXT,
            city TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            metric TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            source_id TEXT,
            track TEXT,
            source_run_id TEXT,
            market_family TEXT,
            event_id TEXT,
            condition_id TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            strategy_key TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'READY','LIVE_ELIGIBLE','BLOCKED','UNKNOWN_BLOCKED'
            )),
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            dependency_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(
                scope_type, city_id, city_timezone, target_local_date,
                temperature_metric, physical_quantity, observation_field,
                data_version, strategy_key, market_family, source_id, track,
                condition_id
            )
        )
        """
    )
    columns = (
        "readiness_id, scope_key, scope_type, city_id, city, city_timezone, "
        "target_local_date, metric, temperature_metric, physical_quantity, "
        "observation_field, data_version, source_id, track, source_run_id, "
        "market_family, event_id, condition_id, token_ids_json, strategy_key, "
        "status, reason_codes_json, computed_at, expires_at, dependency_json, "
        "provenance_json, recorded_at"
    )
    conn.execute(
        f"INSERT INTO readiness_state_status_migrated ({columns}) "
        f"SELECT {columns} FROM readiness_state"
    )
    after = conn.execute("SELECT COUNT(*) FROM readiness_state_status_migrated").fetchone()[0]
    if int(before or 0) != int(after or 0):
        raise RuntimeError(
            "readiness_state status migration row-count mismatch: "
            f"before={before} after={after}"
        )
    conn.execute("DROP TABLE readiness_state")
    conn.execute("ALTER TABLE readiness_state_status_migrated RENAME TO readiness_state")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_readiness_state_entry_scope
            ON readiness_state(city_id, city_timezone, target_local_date,
                               temperature_metric, strategy_key,
                               market_family, condition_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_readiness_state_status_expiry
            ON readiness_state(status, expires_at)
        """
    )


def _table_create_sql(conn: sqlite3.Connection, table_name: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0] or "")


def _assert_no_ambiguous_empty_fallback_rows(
    conn: sqlite3.Connection,
    table_name: str,
    columns: tuple[str, ...],
) -> None:
    predicates = " OR ".join(f"{column} = 'EMPTY_FALLBACK'" for column in columns)
    count = conn.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {predicates}").fetchone()[0]
    if int(count or 0) > 0:
        raise RuntimeError(
            f"{table_name} contains {count} ambiguous EMPTY_FALLBACK row(s); "
            "operator migration must classify them before schema upgrade"
        )


def _migrate_market_topology_state_authority_checks(conn: sqlite3.Connection) -> None:
    leftover_table = "market_topology_state_authority_migrated"
    sql = _table_create_sql(conn, "market_topology_state")
    leftover_sql = _table_create_sql(conn, leftover_table)
    if leftover_sql and (not sql or "EMPTY_FALLBACK" not in sql):
        leftover_count = conn.execute(f"SELECT COUNT(*) FROM {leftover_table}").fetchone()[0]
        if int(leftover_count or 0) > 0:
            raise RuntimeError(
                f"{leftover_table} contains {leftover_count} row(s); refusing to "
                "discard interrupted market_topology_state migration residue"
            )
        conn.execute(f"DROP TABLE {leftover_table}")
    if not sql or "EMPTY_FALLBACK" not in sql:
        return
    _assert_no_ambiguous_empty_fallback_rows(
        conn,
        "market_topology_state",
        ("authority_status", "status"),
    )
    before = conn.execute("SELECT COUNT(*) FROM market_topology_state").fetchone()[0]
    conn.execute("DROP TABLE IF EXISTS market_topology_state_authority_migrated")
    conn.execute(
        """
        CREATE TABLE market_topology_state_authority_migrated (
            topology_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            market_family TEXT NOT NULL,
            event_id TEXT,
            condition_id TEXT NOT NULL,
            question_id TEXT,
            city_id TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            bin_topology_hash TEXT,
            gamma_captured_at TEXT,
            gamma_updated_at TEXT,
            source_contract_status TEXT NOT NULL CHECK (source_contract_status IN (
                'MATCH','MISMATCH','UNKNOWN','QUARANTINED'
            )),
            source_contract_reason TEXT,
            authority_status TEXT NOT NULL CHECK (authority_status IN (
                'VERIFIED','STALE','FETCH_FAILED_NO_CACHE',
                'KEYWORD_DISCOVERY_UNVERIFIED','UNKNOWN'
            )),
            status TEXT NOT NULL CHECK (status IN (
                'CURRENT','STALE','FETCH_FAILED_NO_CACHE',
                'KEYWORD_DISCOVERY_UNVERIFIED','MISMATCH','UNKNOWN'
            )),
            expires_at TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(market_family, condition_id, city_id, target_local_date, temperature_metric, data_version)
        )
        """
    )
    columns = (
        "topology_id, scope_key, market_family, event_id, condition_id, question_id, "
        "city_id, city_timezone, target_local_date, temperature_metric, physical_quantity, "
        "observation_field, data_version, token_ids_json, bin_topology_hash, gamma_captured_at, "
        "gamma_updated_at, source_contract_status, source_contract_reason, authority_status, "
        "status, expires_at, provenance_json, recorded_at"
    )
    conn.execute(
        f"INSERT INTO market_topology_state_authority_migrated ({columns}) "
        f"SELECT {columns} FROM market_topology_state"
    )
    after = conn.execute("SELECT COUNT(*) FROM market_topology_state_authority_migrated").fetchone()[0]
    if int(before or 0) != int(after or 0):
        raise RuntimeError(
            "market_topology_state scan-authority migration row-count mismatch: "
            f"before={before} after={after}"
        )
    conn.execute("DROP TABLE market_topology_state")
    conn.execute(
        "ALTER TABLE market_topology_state_authority_migrated RENAME TO market_topology_state"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_topology_scope
            ON market_topology_state(city_id, city_timezone, target_local_date, temperature_metric, market_family, condition_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_topology_status_expiry
            ON market_topology_state(status, expires_at)
        """
    )


def _migrate_source_contract_audit_authority_checks(conn: sqlite3.Connection) -> None:
    sql = _table_create_sql(conn, "source_contract_audit_events")
    if not sql or "EMPTY_FALLBACK" not in sql:
        return
    _assert_no_ambiguous_empty_fallback_rows(
        conn,
        "source_contract_audit_events",
        ("scan_authority",),
    )
    before = conn.execute("SELECT COUNT(*) FROM source_contract_audit_events").fetchone()[0]
    conn.execute("DROP TRIGGER IF EXISTS source_contract_audit_events_no_update")
    conn.execute("DROP TRIGGER IF EXISTS source_contract_audit_events_no_delete")
    conn.execute("DROP TABLE IF EXISTS source_contract_audit_events_authority_migrated")
    conn.execute(
        """
        CREATE TABLE source_contract_audit_events_authority_migrated (
            audit_id TEXT PRIMARY KEY,
            checked_at_utc TEXT NOT NULL,
            scan_authority TEXT NOT NULL CHECK (scan_authority IN (
                'VERIFIED','FIXTURE','STALE_CACHE','FETCH_FAILED_NO_CACHE',
                'KEYWORD_DISCOVERY_UNVERIFIED','NEVER_FETCHED'
            )),
            report_status TEXT CHECK (report_status IS NULL OR report_status IN (
                'OK','WARN','ALERT','DATA_UNAVAILABLE'
            )),
            severity TEXT NOT NULL CHECK (severity IN ('OK','WARN','ALERT','DATA_UNAVAILABLE')),
            event_id TEXT,
            slug TEXT,
            title TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            source_contract_status TEXT NOT NULL CHECK (source_contract_status IN (
                'MATCH','MISSING','AMBIGUOUS','MISMATCH','UNSUPPORTED','UNKNOWN','QUARANTINED'
            )),
            source_contract_reason TEXT,
            configured_source_family TEXT,
            configured_station_id TEXT,
            observed_source_family TEXT,
            observed_station_id TEXT,
            resolution_sources_json TEXT NOT NULL DEFAULT '[]',
            source_contract_json TEXT NOT NULL DEFAULT '{}',
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
        )
        """
    )
    columns = (
        "audit_id, checked_at_utc, scan_authority, report_status, severity, event_id, "
        "slug, title, city, target_date, temperature_metric, source_contract_status, "
        "source_contract_reason, configured_source_family, configured_station_id, "
        "observed_source_family, observed_station_id, resolution_sources_json, "
        "source_contract_json, payload_hash, created_at"
    )
    conn.execute(
        f"INSERT INTO source_contract_audit_events_authority_migrated ({columns}) "
        f"SELECT {columns} FROM source_contract_audit_events"
    )
    after = conn.execute(
        "SELECT COUNT(*) FROM source_contract_audit_events_authority_migrated"
    ).fetchone()[0]
    if int(before or 0) != int(after or 0):
        raise RuntimeError(
            "source_contract_audit_events scan-authority migration row-count mismatch: "
            f"before={before} after={after}"
        )
    conn.execute("DROP TABLE source_contract_audit_events")
    conn.execute(
        "ALTER TABLE source_contract_audit_events_authority_migrated "
        "RENAME TO source_contract_audit_events"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_source_contract_audit_city_date
            ON source_contract_audit_events(city, target_date, temperature_metric, checked_at_utc)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_source_contract_audit_status
            ON source_contract_audit_events(source_contract_status, severity, checked_at_utc)
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS source_contract_audit_events_no_update
        BEFORE UPDATE ON source_contract_audit_events
        BEGIN
          SELECT RAISE(ABORT, 'source_contract_audit_events is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS source_contract_audit_events_no_delete
        BEFORE DELETE ON source_contract_audit_events
        BEGIN
          SELECT RAISE(ABORT, 'source_contract_audit_events is append-only');
        END
        """
    )


def _migrate_trade_strategy_key_checks(conn: sqlite3.Connection) -> None:
    """Remove stale hardcoded strategy_key CHECK from trade-class tables.

    Finding 6 (P2, 2026-05-22): position_events, position_current,
    execution_fact, and trade-rooted opportunity_fact carried the same stale
    4-strategy CHECK. Triggers and explicit indexes are preserved via
    sqlite_master query+recreate.
    """
    import re as _re  # noqa: F811
    for tname in ("position_events", "position_current", "execution_fact", "opportunity_fact"):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tname,)
        ).fetchone()
        if row is None:
            continue
        old_sql = str(row[0])
        if "'opening_inertia'" not in old_sql or "day0_nowcast_entry" in old_sql:
            continue  # Already migrated or no stale CHECK
        triggers = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND sql IS NOT NULL",
            (tname,),
        ).fetchall()
        indexes = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (tname,),
        ).fetchall()
        new_create = _strip_strategy_key_check(old_sql)
        new_create = _re.sub(
            rf"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+{_re.escape(tname)}\b",
            f"CREATE TABLE {tname}_new",
            new_create,
        )
        conn.execute(f"DROP TABLE IF EXISTS {tname}_new")
        conn.execute(new_create)
        conn.execute(f"INSERT INTO {tname}_new SELECT * FROM {tname}")
        conn.execute(f"DROP TABLE {tname}")
        _legacy_alter_was_enabled = _legacy_alter_table_enabled(conn)
        _set_legacy_alter_table(conn, True)
        try:
            conn.execute(f"ALTER TABLE {tname}_new RENAME TO {tname}")
        finally:
            _set_legacy_alter_table(conn, _legacy_alter_was_enabled)
        for (trg_sql,) in triggers:
            conn.execute(trg_sql)
        for (idx_sql,) in indexes:
            conn.execute(idx_sql)



def _create_source_time_frontier(conn: sqlite3.Connection) -> None:
    """Create the persisted source-time frontier table (PR #329 D). Idempotent.

    Forecasts-class. The online authority for "latest USABLE data per source/family right now":
    live health reads THIS instead of recomputing from scratch in ad-hoc scripts. The writer
    (src.data.frontier_store.persist_frontier) UPSERTs keyed by (source_id, family,
    partition_key) so a re-tick for the same partition is idempotent, and refuses to let a
    backfill/reconstructed authority overwrite a live (DERIVED_FROM_DISSEMINATION) row.

    latest_event_time is the SOURCE/EVENT-time plane (issue/target/settled), NEVER a write-time
    column — the load-bearing freshness rule. computed_at is the write time (provenance only).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_time_frontier (
            source_id TEXT NOT NULL,
            family TEXT NOT NULL,
            partition_key TEXT NOT NULL,
            track TEXT,
            role TEXT,
            latest_event_time TEXT,
            freshness_state TEXT,
            live_blocker TEXT,
            authority_tier TEXT NOT NULL DEFAULT 'UNVERIFIED',
            computed_at TEXT NOT NULL,
            data_version INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (source_id, family, partition_key)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_time_frontier_family "
        "ON source_time_frontier (family, source_id)"
    )


def init_schema_forecasts(conn: sqlite3.Connection) -> None:
    """Create all forecast-authority tables on zeus-forecasts.db. Idempotent.

    Schema-replication strategy (Option A, 2026-05-11 schema-drift antibody):
    Instead of maintaining parallel CREATE TABLE DDL that diverges from world.db
    via ALTER TABLE migrations over time, we ATTACH world.db read-only and copy
    its sqlite_master CREATE TABLE + CREATE INDEX statements directly.  This
    guarantees byte-identical schema parity regardless of past or future ALTERs
    applied to world.db.

    Fresh-deploy fallback: if world.db does not exist yet (e.g., test env that
    only bootstraps forecasts.db), we fall back to the static _create_*
    helpers.  The fallback DDL must stay in sync manually; the ATTACH path is
    the authoritative production path.

    Tables owned by this DB after the live data-daemon authority split
    (derived from registry P2):
      ensemble_snapshots, source_run, source_run_coverage,
      producer readiness_state, job_run, observations, settlements,
      calibration_pairs, settlement_outcomes, market_events

    K1 split 2026-05-11 — do NOT call init_schema() on the forecasts conn;
    that would create world-class tables on the wrong DB.

    P2 DDL refactor (2026-05-14): table list derived from registry
    (tables_for_class(FORECAST_CLASS)) instead of hardcoded _FORECAST_TABLES.
    Stop-condition #8: ATTACH branch iterates registry, not raw sqlite_master.
    """
    from src.state.table_registry import SchemaClass as _SchemaClass, tables_for_class as _tables_for_class
    _registry_forecast_tables: frozenset[str] = _tables_for_class(_SchemaClass.FORECAST_CLASS)

    # K1-ghost exclusion: tables that are forecast-class-owned and must NEVER be
    # sourced from world_src.sqlite_master, even if a stale ghost row exists there.
    # market_events: B3cont (PR3) collapsed market_events_v2 → market_events on
    # forecasts.db; the old v1 shell on world.db (0 rows, missing temperature_metric
    # + recorded_at) was declared legacy_archived but not yet dropped from the live
    # file. If the ATTACH path picks up that ghost DDL and then _ensure_forecast_indexes
    # runs CREATE INDEX ON market_events(temperature_metric), it crashes with
    # "no such column: temperature_metric". Fix: always build via _create_market_events
    # (canonical static DDL in v2_schema.py) and never copy from world_src.
    _WORLD_ATTACH_EXCLUDED: frozenset[str] = frozenset({"market_events"})

    # Opt-in TypedConnection identity guard (P2): if a TypedConnection is
    # passed, verify it wraps the forecasts DB. Raw sqlite3.Connection callers
    # are accepted without check (P3 migrates all call sites to ForecastsConnection).
    from src.state.connection_pair import TypedConnection as _TypedConnection
    from src.state.table_registry import DBIdentity as _DBIdentity
    if isinstance(conn, _TypedConnection) and conn.db_identity != _DBIdentity.FORECASTS:
        raise ValueError(
            f"init_schema_forecasts received a TypedConnection for "
            f"{conn.db_identity!r} — must be DBIdentity.FORECASTS. "
            f"Pass a ForecastsConnection or a raw sqlite3.Connection."
        )

    _busy_ms = int(os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))
    conn.execute(f"PRAGMA busy_timeout = {_busy_ms}")

    world_path = str(ZEUS_WORLD_DB_PATH)
    # Fingerprint-safety: when called with a :memory: connection (e.g.
    # check_schema_fingerprint.py), always use the static DDL path.  The ATTACH
    # path copies schema from the live zeus-world.db file, making the fingerprint
    # environment-dependent (different on dev machines vs CI).  Static DDL is the
    # only reproducible source of truth for code-plane schema checks.
    _conn_is_memory = conn.execute(
        "SELECT file FROM pragma_database_list WHERE name='main'"
    ).fetchone()[0] in ("", ":memory:")
    if not _conn_is_memory and ZEUS_WORLD_DB_PATH.exists() and ZEUS_WORLD_DB_PATH.stat().st_size > 0:
        # --- Production path: replicate schema from world.db sqlite_master ---
        # P2 stop-condition #8: iterate registry (not raw world_src.sqlite_master)
        # so ATTACH path and static-helpers path are always in sync with the
        # canonical ownership declaration.
        conn.execute(f"ATTACH DATABASE '{world_path}' AS world_src")
        try:
            for tbl in _registry_forecast_tables:
                if tbl in _WORLD_ATTACH_EXCLUDED:
                    # Never copy from world_src; always built via canonical static DDL.
                    continue
                row = conn.execute(
                    "SELECT sql FROM world_src.sqlite_master"
                    " WHERE type='table' AND name=?",
                    (tbl,),
                ).fetchone()
                if row and row[0]:
                    # sqlite_master sql is always "CREATE TABLE …"; make idempotent
                    ddl = row[0].replace(
                        "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1
                    )
                    conn.execute(ddl)

            for tbl in _registry_forecast_tables:
                if tbl in _WORLD_ATTACH_EXCLUDED:
                    # Indexes for excluded tables are built by their static helper.
                    continue
                for (sql,) in conn.execute(
                    "SELECT sql FROM world_src.sqlite_master"
                    " WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                    (tbl,),
                ).fetchall():
                    idx_ddl = sql.replace(
                        "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1
                    ).replace(
                        "CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1
                    )
                    conn.execute(idx_ddl)
        finally:
            conn.execute("DETACH DATABASE world_src")

        # K1-ghost exclusion: build excluded tables from canonical static DDL
        # unconditionally, so a stale world ghost can never poison forecasts schema.
        from src.state.schema.v2_schema import _create_market_events as _cme
        _cme(conn)

        # Post-P2 fallback: world.db may be world-class-only (no legacy forecast
        # table copies). Any registry forecast table not yet on forecasts conn
        # must be created via static helpers so _ensure_forecast_indexes succeeds.
        _missing_on_fc = {
            tbl for tbl in _registry_forecast_tables
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
        }
        if _missing_on_fc:
            _create_observations(conn)
            _create_source_run(conn)
            from src.state.schema.v2_schema import (
                _create_settlement_outcomes,
                _create_market_events,
                _create_ensemble_snapshots,
                _create_calibration_pairs,
            )
            _create_settlement_outcomes(conn)
            _create_market_events(conn)
            _create_ensemble_snapshots(conn)
            _create_calibration_pairs(conn)
    else:
        # --- Fresh-deploy / :memory: fallback: static helpers ---
        # Two cases reach here:
        #   1. :memory: connection (e.g. check_schema_fingerprint.py) — intentional;
        #      ATTACH path would make fingerprint environment-dependent.
        #   2. Fresh deploy where world.db does not yet exist.
        import logging as _logging
        if not _conn_is_memory:
            _logging.getLogger(__name__).warning(
                "init_schema_forecasts: world.db not found at %s; "
                "falling back to static DDL helpers.  Schema may drift from "
                "production world.db if ALTER TABLE migrations were not applied.",
                world_path,
            )
        _create_observations(conn)
        _create_source_run(conn)
        _create_job_run(conn)
        _create_source_run_coverage(conn)
        _create_readiness_state(conn)

        from src.state.schema.v2_schema import (
            _create_settlement_outcomes,
            _create_market_events,
            _create_ensemble_snapshots,
            _create_calibration_pairs,
        )
        _create_settlement_outcomes(conn)
        _create_market_events(conn)
        _create_ensemble_snapshots(conn)
        _create_calibration_pairs(conn)

    # Forecast authority post-condition: older world.db schemas or partial K1
    # copies can omit the live source-chain tables. These helpers are
    # idempotent and also backfill the hot-path indexes after ATTACH copying.
    _create_job_run(conn)
    _ensure_job_run_release_key_identity(conn)
    _create_source_run_coverage(conn)
    _create_readiness_state(conn)
    _migrate_readiness_state_status_checks(conn)

    # Post-condition equivalence (Option A, 2026-05-14):
    # The ATTACH branch above copies indexes from world_src.sqlite_master; if
    # world.db trails v2_schema.py (partial migration, init_schema_world_only
    # deploy, or test fixture), critical v2 covering indexes silently go
    # missing on the forecasts conn. Run the idempotent helper unconditionally
    # so both branches converge to the canonical v2 index inventory.
    # PLAN-evidence: docs/operations/task_2026-05-14_attach_path_index_fix/PLAN.md
    _ensure_forecast_indexes(conn)

    # T2 Day0Nowcast — SCHEMA_FORECASTS_VERSION 4 (2026-05-19).
    # These tables are forecast-class only; not in world_src so always created
    # via static helpers (ATTACH branch will not find them in world_src.sqlite_master).
    _create_day0_horizon_platt_fits(conn)
    _create_day0_nowcast_runs(conn)

    # T4 MarketAnalysisVNext — SCHEMA_FORECASTS_VERSION 5 (2026-05-21).
    # ALTER TABLE migrations for F4 bin_grid_id retrofit + new microstructure table.
    # Guard pattern: swallow "duplicate column" on already-migrated DBs.
    for _alter_sql in (
        "ALTER TABLE day0_nowcast_runs ADD COLUMN bin_grid_id TEXT",
        "ALTER TABLE day0_nowcast_runs ADD COLUMN bin_schema_id TEXT",
        # ThePath P1 ITEM 1 (2026-06-07): obs-availability instrumentation on the
        # Day0 nowcast lane. Nullable TEXT, no default — ADD COLUMN is non-rewriting,
        # idempotent on already-migrated DBs (duplicate-column swallow below).
        # SQLite cannot ADD a CHECK constraint via ALTER, so the provenance vocab
        # CHECK is enforced at the writer (write_nowcast_run) for existing DBs and
        # in the CREATE TABLE above for fresh DBs.
        "ALTER TABLE day0_nowcast_runs ADD COLUMN observation_available_at TEXT",
        "ALTER TABLE day0_nowcast_runs ADD COLUMN obs_availability_provenance TEXT",
    ):
        try:
            conn.execute(_alter_sql)
        except sqlite3.OperationalError as _exc:
            if "duplicate column" not in str(_exc).lower():
                raise

    _create_market_microstructure_snapshots(conn)

    # Phase 7 T1 — SCHEMA_FORECASTS_VERSION 6 (2026-05-21).
    # Add outcome_type column to settlement_outcomes; guard for already-migrated DBs.
    # B3cont (2026-05-28): table renamed from settlement_outcomes.
    try:
        conn.execute("ALTER TABLE settlement_outcomes ADD COLUMN outcome_type INTEGER")
    except sqlite3.OperationalError as _exc:
        if "duplicate column" not in str(_exc).lower():
            raise

    # A8/A9 split (consult 6a42bc3d, 2026-06-29): add the canonical event-lifecycle
    # column alongside the legacy fused outcome_type. Nullable; readers fall back to the
    # legacy outcome_type mapping when absent (zero behavior change). log_settlement's
    # INSERT is intentionally NOT changed — population is a later writer/backfill step.
    try:
        conn.execute(
            "ALTER TABLE settlement_outcomes ADD COLUMN resolution_state TEXT "
            "CHECK (resolution_state IS NULL OR resolution_state IN ("
            "'UNRESOLVED', 'PHYSICALLY_CONFIRMED', 'SOURCE_PUBLISHED_VENUE_UNRESOLVED', "
            "'VENUE_RESOLVED', 'OBSERVATION_REVISED', 'DISPUTED', 'VOID_50_50', 'SOURCE_REVISION'))"
        )
    except sqlite3.OperationalError as _exc:
        if "duplicate column" not in str(_exc).lower():
            raise

    # Phase 7 T3 — SCHEMA_FORECASTS_VERSION 6 (2026-05-21).
    # settlement_capture_verifications: forecasts-only, not in world_src.sqlite_master.
    # Follow _create_market_microstructure_snapshots precedent: static helper, no registry add.
    from src.state.schema.v2_schema import _create_settlement_capture_verifications as _create_scv
    _create_scv(conn)

    # Data Temporal Kernel — SCHEMA_FORECASTS_VERSION 7 (2026-05-24, PR #329 D).
    # Persisted source-time frontier authority; forecasts-only static helper (not in world_src).
    _create_source_time_frontier(conn)

    # Replacement forecast live-support provenance (2026-06-07).
    # Forecasts-only static helper; never copied from world_src and never
    # created on world/trade DBs. Keeps boot-time registry equality aligned
    # with apply_canonical_schema(forecast_tables=True).
    from src.state.schema.v2_schema import (
        _create_replacement_forecast_live_tables as _create_replacement_live,
    )
    _create_replacement_live(conn)

    conn.commit()


def init_schema_world_only(conn: Optional[sqlite3.Connection] = None) -> None:
    """Create world-class tables on zeus-world.db. Idempotent.

    Post-K1 split (2026-05-11): init_schema creates world-class tables plus
    legacy_archived ghost copies of forecast-class tables (see init_schema()
    docstring). This function is an alias for init_schema() preserved for
    call-site clarity. New forecast-class tables live on zeus-forecasts.db
    via init_schema_forecasts.

    The world executescript block still contains settlements/observations/
    source_run CREATE IF NOT EXISTS — those are idempotent no-ops post-migration
    after §5.4 renames them away.

    P2 DDL refactor 2026-05-14.
    """
    init_schema(conn)


# ---------------------------------------------------------------------------
# Trade-class table names — authoritative list for set-equality checks.
# Kept adjacent to init_schema_trade_only to prevent silent divergence.
# ---------------------------------------------------------------------------
_TRADE_CLASS_TABLES: frozenset[str] = frozenset({
    "_migrations_applied",
    "book_hash_transitions",
    "decision_integrity_quarantine",
    "execution_fact",
    "execution_feasibility_evidence",
    "executable_market_snapshots",
    # Repoint 2 (fix/prearm-fill-exit-readiness 2026-06-03): outcome_fact
    # corrected to trade_class. The live writer (harvester.py log_settlement_event)
    # has always written to zeus_trades.db via trade_conn (18 live rows confirmed,
    # 0 on zeus-world.db). DDL added to _TRADE_CLASS_DDL below so init_schema_trade_only
    # creates it on fresh DBs. architecture/db_table_ownership.yaml updated to match.
    "outcome_fact",
    "position_current",
    "position_events",
    "position_lots",
    "settlement_command_events",
    "settlement_commands",
    "settlement_day_observation_authority",
    # W2.4 (2026-07-02): ctf_conversion_commands DDL lives in
    # src/execution/ctf_conversion_commands.py (same pattern as
    # SETTLEMENT_COMMAND_SCHEMA) — imported once below, not duplicated.
    "ctf_conversion_command_events",
    "ctf_conversion_commands",
    "trade_decisions",
    "venue_command_events",
    "venue_commands",
    "venue_order_facts",
    "venue_submission_envelopes",
    "venue_trade_facts",
    # PR-S4b completion 2026-07-01: 13 heritage tables converged to trade_class
    # (data 100% on zeus_trades.db; world ghosts 0-row legacy_archived). DDL now in
    # _TRADE_CLASS_DDL above so fresh init_schema_trade_only creates them.
    "collateral_ledger_snapshots",
    "collateral_reservations",
    "decision_log",
    "exchange_reconcile_findings",
    "exit_mutex_holdings",
    "market_price_history",
    "opportunity_fact",
    "provenance_envelope_events",
    "risk_actions",
    "strategy_health",
    "token_price_log",
    "token_suppression",
    "token_suppression_history",
})

# DDL for the 9 trade-class tables whose CREATE TABLE lives in db.py /
# execution/settlement_commands.py / the kernel SQL.
# Copied verbatim from their authoritative sources so this constructor is
# self-contained and does not pull world-class tables along.
_TRADE_CLASS_DDL = """
-- position_events + triggers (from architecture/2026_04_02_architecture_kernel.sql)
CREATE TABLE IF NOT EXISTS position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT',
        'ENTRY_ORDER_POSTED',
        'ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_VOIDED',
        'ENTRY_ORDER_REJECTED',
        'DAY0_WINDOW_ENTERED',
        'CHAIN_SYNCED',
        'CHAIN_SIZE_CORRECTED',
        'CHAIN_QUARANTINED',
        'MONITOR_REFRESHED',
        'EXIT_INTENT',
        'EXIT_ORDER_POSTED',
        'EXIT_ORDER_FILLED',
        'EXIT_ORDER_VOIDED',
        'EXIT_ORDER_REJECTED',
        'EXIT_RETRY_RELEASED',
        'SETTLED',
        'ADMIN_VOIDED',
        'MANUAL_OVERRIDE_APPLIED',
        'VENUE_POSITION_OBSERVED',
        'REVIEW_REQUIRED'
    )),
    occurred_at TEXT NOT NULL
        CHECK (occurred_at LIKE '____-__-__T%' OR occurred_at = 'QUARANTINE'),
    phase_before TEXT CHECK (phase_before IS NULL OR phase_before IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','quarantined','admin_closed'
    )),
    phase_after TEXT CHECK (phase_after IS NULL OR phase_after IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','quarantined','admin_closed'
    )),
    strategy_key TEXT NOT NULL,
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL,
    env TEXT NOT NULL CHECK (env IN ('live','test','replay','backtest')),
    payload_json TEXT NOT NULL,
    UNIQUE(position_id, sequence_no)
);
CREATE TRIGGER IF NOT EXISTS trg_position_events_require_env
BEFORE INSERT ON position_events
WHEN NEW.env IS NULL OR TRIM(NEW.env) = ''
BEGIN
    SELECT RAISE(FAIL, 'position_events.env is required');
END;
CREATE TRIGGER IF NOT EXISTS trg_position_events_no_update
BEFORE UPDATE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_position_events_no_delete
BEFORE DELETE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END;

-- position_current (from architecture/2026_04_02_architecture_kernel.sql)
CREATE TABLE IF NOT EXISTS position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL CHECK (phase IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','quarantined','admin_closed'
    )),
    trade_id TEXT,
    market_id TEXT,
    city TEXT,
    cluster TEXT,
    target_date TEXT,
    bin_label TEXT,
    direction TEXT CHECK (direction IS NULL OR direction IN ('buy_yes','buy_no','unknown')),
    unit TEXT CHECK (unit IS NULL OR unit IN ('F','C')),
    size_usd REAL,
    shares REAL,
    cost_basis_usd REAL,
    entry_price REAL,
    p_posterior REAL,
    entry_ci_width REAL,
    last_monitor_prob REAL,
    last_monitor_prob_is_fresh INTEGER CHECK (
        last_monitor_prob_is_fresh IS NULL OR last_monitor_prob_is_fresh IN (0,1)
    ),
    last_monitor_edge REAL,
    last_monitor_market_price REAL,
    last_monitor_market_price_is_fresh INTEGER CHECK (
        last_monitor_market_price_is_fresh IS NULL OR last_monitor_market_price_is_fresh IN (0,1)
    ),
    last_monitor_best_bid REAL,
    last_monitor_best_ask REAL,
    last_monitor_market_vig REAL,
    decision_snapshot_id TEXT,
    entry_method TEXT,
    strategy_key TEXT NOT NULL,
    edge_source TEXT,
    discovery_mode TEXT,
    chain_state TEXT,
    token_id TEXT,
    no_token_id TEXT,
    condition_id TEXT,
    order_id TEXT,
    order_status TEXT,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
    -- PR D0b (Finding D0/D2-wire, Part-2 audit, 2026-05-27): durable
    -- authority projection. NULL-default so the columns are additive on
    -- legacy DBs via ALTER TABLE ADD COLUMN (src/state/ledger.py
    -- _ensure_position_current_authority_columns). Downstream training
    -- gates and crash-recovery loaders consult these fields to
    -- distinguish balance-only recovery from trade-verified fill.
    fill_authority TEXT,
    recovery_authority TEXT,
    chain_shares REAL,
    -- F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain-observed
    -- economics columns so balance-only rescued positions persist
    -- venue truth without overwriting submitted entry economics. Additive
    -- on legacy DBs via _ensure_position_current_authority_columns.
    chain_avg_price REAL,
    chain_cost_basis_usd REAL,
    chain_seen_at TEXT,
    chain_absence_at TEXT,
    -- BUG #128 (SEV1, 2026-06-02): durable realized-P&L projection. Pre-fix,
    -- realized P&L lived ONLY on the in-memory Position object + positions.json
    -- recent_exits[]; a filled+settled order left NO queryable P&L record. These
    -- nullable columns persist the close economics through the canonical write
    -- path (build_position_current_projection) so GOAL#36 post-fill correctness
    -- checks have a durable source of truth. NULL on open/legacy rows; populated
    -- at economic-close / settlement. Additive on legacy DBs via
    -- _ensure_position_current_authority_columns.
    realized_pnl_usd REAL,
    exit_price REAL,
    settlement_price REAL,
    settled_at TEXT,
    exit_reason TEXT
);

-- execution_fact (from architecture/2026_04_02_architecture_kernel.sql)
CREATE TABLE IF NOT EXISTS execution_fact (
    intent_id TEXT PRIMARY KEY,
    position_id TEXT,
    decision_id TEXT,
    order_role TEXT NOT NULL CHECK (order_role IN ('entry','exit')),
    strategy_key TEXT,
    posted_at TEXT,
    filled_at TEXT,
    voided_at TEXT,
    submitted_price REAL,
    fill_price REAL,
    shares REAL,
    fill_quality REAL,
    latency_seconds REAL,
    venue_status TEXT,
    terminal_exec_status TEXT,
    command_id TEXT,
    -- H2_E2E (REAUDIT_0_1.md §2/§4): fill->posterior link. Nullable, no DEFAULT;
    -- populated in FILL reconciliation by joining edli_no_submit_receipts on
    -- final_intent_id (replacement_0_1 orders only). NULL on every other order so
    -- this never alters mainline/canonical execution facts (observability only).
    posterior_id INTEGER
);

-- trade_decisions (from src/state/db.py:init_schema executescript block)
CREATE TABLE IF NOT EXISTS trade_decisions (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    bin_label TEXT NOT NULL,
    direction TEXT NOT NULL,
    size_usd REAL NOT NULL,
    price REAL NOT NULL,
    timestamp TEXT NOT NULL,
    forecast_snapshot_id INTEGER,
    calibration_model_version TEXT,
    p_raw REAL NOT NULL,
    p_calibrated REAL,
    p_posterior REAL NOT NULL,
    edge REAL NOT NULL,
    ci_lower REAL NOT NULL,
    ci_upper REAL NOT NULL,
    kelly_fraction REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    filled_at TEXT,
    fill_price REAL,
    runtime_trade_id TEXT,
    order_id TEXT,
    order_status_text TEXT,
    order_posted_at TEXT,
    entered_at_ts TEXT,
    chain_state TEXT,
    strategy TEXT,
    edge_source TEXT,
    bin_type TEXT,
    env TEXT NOT NULL DEFAULT 'live',
    discovery_mode TEXT,
    market_hours_open REAL,
    fill_quality REAL,
    entry_method TEXT,
    selected_method TEXT,
    applied_validations_json TEXT,
    exit_trigger TEXT,
    exit_reason TEXT,
    admin_exit_reason TEXT,
    exit_divergence_score REAL DEFAULT 0.0,
    exit_market_velocity_1h REAL DEFAULT 0.0,
    exit_forward_edge REAL DEFAULT 0.0,
    settlement_semantics_json TEXT,
    epistemic_context_json TEXT,
    edge_context_json TEXT,
    entry_alpha_usd REAL DEFAULT 0.0,
    execution_slippage_usd REAL DEFAULT 0.0,
    exit_timing_usd REAL DEFAULT 0.0,
    risk_throttling_usd REAL DEFAULT 0.0,
    settlement_edge_usd REAL DEFAULT 0.0
);

-- venue_submission_envelopes + triggers (from src/state/db.py:init_provenance_projection_schema)
CREATE TABLE IF NOT EXISTS venue_submission_envelopes (
  envelope_id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL DEFAULT 1,
  sdk_package TEXT NOT NULL,
  sdk_version TEXT NOT NULL,
  host TEXT NOT NULL,
  chain_id INTEGER NOT NULL,
  funder_address TEXT NOT NULL,
  condition_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  yes_token_id TEXT NOT NULL,
  no_token_id TEXT NOT NULL,
  selected_outcome_token_id TEXT NOT NULL,
  outcome_label TEXT NOT NULL CHECK (outcome_label IN ('YES','NO')),
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  price TEXT NOT NULL,
  size TEXT NOT NULL,
  order_type TEXT NOT NULL CHECK (order_type IN ('GTC','GTD','FOK','FAK')),
  post_only INTEGER NOT NULL CHECK (post_only IN (0,1)),
  tick_size TEXT NOT NULL,
  min_order_size TEXT NOT NULL,
  neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
  fee_details_json TEXT NOT NULL,
  canonical_pre_sign_payload_hash TEXT NOT NULL,
  signed_order_blob BLOB,
  signed_order_hash TEXT,
  raw_request_hash TEXT NOT NULL,
  raw_response_json TEXT,
  order_id TEXT,
  trade_ids_json TEXT NOT NULL DEFAULT '[]',
  transaction_hashes_json TEXT NOT NULL DEFAULT '[]',
  error_code TEXT,
  error_message TEXT,
  captured_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS venue_submission_envelopes_no_update
BEFORE UPDATE ON venue_submission_envelopes
BEGIN
  SELECT RAISE(ABORT, 'venue_submission_envelopes is append-only');
END;
CREATE TRIGGER IF NOT EXISTS venue_submission_envelopes_no_delete
BEFORE DELETE ON venue_submission_envelopes
BEGIN
  SELECT RAISE(ABORT, 'venue_submission_envelopes is append-only');
END;

-- venue_order_facts + indexes + triggers (from src/state/db.py:init_provenance_projection_schema)
CREATE TABLE IF NOT EXISTS venue_order_facts (
  fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
  venue_order_id TEXT NOT NULL,
  command_id TEXT NOT NULL REFERENCES venue_commands(command_id),
  state TEXT NOT NULL CHECK (state IN (
    'LIVE','RESTING','MATCHED','PARTIALLY_MATCHED',
    'CANCEL_REQUESTED','CANCEL_CONFIRMED','CANCEL_UNKNOWN','CANCEL_FAILED',
    'EXPIRED','VENUE_WIPED','HEARTBEAT_CANCEL_SUSPECTED'
  )),
  remaining_size TEXT,
  matched_size TEXT,
  source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
  observed_at TEXT NOT NULL,
  venue_timestamp TEXT,
  ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
  local_sequence INTEGER NOT NULL,
  raw_payload_hash TEXT NOT NULL,
  raw_payload_json TEXT,
  UNIQUE (venue_order_id, local_sequence)
);
CREATE INDEX IF NOT EXISTS idx_order_facts_command ON venue_order_facts (command_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_order_facts_state ON venue_order_facts (state, observed_at);
CREATE TRIGGER IF NOT EXISTS venue_order_facts_no_update
BEFORE UPDATE ON venue_order_facts
BEGIN
  SELECT RAISE(ABORT, 'venue_order_facts is append-only');
END;
CREATE TRIGGER IF NOT EXISTS venue_order_facts_no_delete
BEFORE DELETE ON venue_order_facts
BEGIN
  SELECT RAISE(ABORT, 'venue_order_facts is append-only');
END;

-- venue_trade_facts + indexes + triggers (from src/state/db.py:init_provenance_projection_schema)
CREATE TABLE IF NOT EXISTS venue_trade_facts (
  trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_id TEXT NOT NULL,
  venue_order_id TEXT NOT NULL,
  command_id TEXT NOT NULL REFERENCES venue_commands(command_id),
  state TEXT NOT NULL CHECK (state IN ('MATCHED','MINED','CONFIRMED','RETRYING','FAILED')),
  filled_size TEXT NOT NULL,
  fill_price TEXT NOT NULL,
  fee_paid_micro INTEGER,
  tx_hash TEXT,
  block_number INTEGER,
  confirmation_count INTEGER DEFAULT 0,
  source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
  observed_at TEXT NOT NULL,
  venue_timestamp TEXT,
  ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
  local_sequence INTEGER NOT NULL,
  raw_payload_hash TEXT NOT NULL,
  raw_payload_json TEXT,
  UNIQUE (trade_id, local_sequence)
);
CREATE INDEX IF NOT EXISTS idx_trade_facts_command ON venue_trade_facts (command_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_trade_facts_trade ON venue_trade_facts (trade_id, observed_at);
CREATE TRIGGER IF NOT EXISTS venue_trade_facts_no_update
BEFORE UPDATE ON venue_trade_facts
BEGIN
  SELECT RAISE(ABORT, 'venue_trade_facts is append-only');
END;
CREATE TRIGGER IF NOT EXISTS venue_trade_facts_no_delete
BEFORE DELETE ON venue_trade_facts
BEGIN
  SELECT RAISE(ABORT, 'venue_trade_facts is append-only');
END;

-- position_lots + indexes + triggers (from src/state/db.py:init_provenance_projection_schema)
CREATE TABLE IF NOT EXISTS position_lots (
  lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN (
    'OPTIMISTIC_EXPOSURE','CONFIRMED_EXPOSURE',
    'EXIT_PENDING','ECONOMICALLY_CLOSED_OPTIMISTIC',
    'ECONOMICALLY_CLOSED_CONFIRMED','SETTLED','QUARANTINED'
  )),
  shares TEXT NOT NULL,
  entry_price_avg TEXT NOT NULL,
  exit_price_avg TEXT,
  source_command_id TEXT REFERENCES venue_commands(command_id),
  source_trade_fact_id INTEGER REFERENCES venue_trade_facts(trade_fact_id),
  captured_at TEXT NOT NULL,
  state_changed_at TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
  observed_at TEXT NOT NULL,
  venue_timestamp TEXT,
  ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
  local_sequence INTEGER NOT NULL,
  raw_payload_hash TEXT NOT NULL,
  raw_payload_json TEXT,
  UNIQUE (position_id, local_sequence)
);
CREATE INDEX IF NOT EXISTS idx_position_lots_state ON position_lots (state, position_id);
CREATE INDEX IF NOT EXISTS idx_position_lots_trade ON position_lots (source_trade_fact_id);
CREATE TRIGGER IF NOT EXISTS position_lots_optimistic_trade_authority
BEFORE INSERT ON position_lots
WHEN NEW.state = 'OPTIMISTIC_EXPOSURE'
BEGIN
  SELECT RAISE(ABORT, 'OPTIMISTIC_EXPOSURE requires MATCHED/MINED source trade fact authority')
  WHERE NOT EXISTS (
    SELECT 1
      FROM venue_trade_facts tf
      JOIN venue_commands cmd ON cmd.command_id = tf.command_id
     WHERE tf.trade_fact_id = NEW.source_trade_fact_id
       AND tf.command_id = NEW.source_command_id
       AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
       AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
       AND tf.state IN ('MATCHED','MINED')
       AND zeus_positive_decimal_text(tf.filled_size) = 1
       AND zeus_positive_decimal_text(tf.fill_price) = 1
       AND zeus_decimal_text_equal(tf.filled_size, NEW.shares) = 1
       AND zeus_decimal_text_equal(tf.fill_price, NEW.entry_price_avg) = 1
  );
END;
CREATE TRIGGER IF NOT EXISTS position_lots_confirmed_trade_authority
BEFORE INSERT ON position_lots
WHEN NEW.state = 'CONFIRMED_EXPOSURE'
BEGIN
  SELECT RAISE(ABORT, 'CONFIRMED_EXPOSURE requires CONFIRMED source trade fact authority')
  WHERE NOT EXISTS (
    SELECT 1
      FROM venue_trade_facts tf
      JOIN venue_commands cmd ON cmd.command_id = tf.command_id
     WHERE tf.trade_fact_id = NEW.source_trade_fact_id
       AND tf.command_id = NEW.source_command_id
       AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
       AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
       AND tf.state = 'CONFIRMED'
       AND zeus_positive_decimal_text(tf.filled_size) = 1
       AND zeus_positive_decimal_text(tf.fill_price) = 1
       AND zeus_decimal_text_equal(tf.filled_size, NEW.shares) = 1
       AND zeus_decimal_text_equal(tf.fill_price, NEW.entry_price_avg) = 1
  );
END;
CREATE TRIGGER IF NOT EXISTS position_lots_no_update
BEFORE UPDATE ON position_lots
BEGIN
  SELECT RAISE(ABORT, 'position_lots is append-only');
END;
CREATE TRIGGER IF NOT EXISTS position_lots_no_delete
BEFORE DELETE ON position_lots
BEGIN
  SELECT RAISE(ABORT, 'position_lots is append-only');
END;

-- venue_commands + indexes (from src/state/db.py:init_schema executescript block)
CREATE TABLE IF NOT EXISTS venue_commands (
    command_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    envelope_id TEXT NOT NULL,
    position_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    intent_kind TEXT NOT NULL,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    price REAL NOT NULL,
    venue_order_id TEXT,
    state TEXT NOT NULL,
    last_event_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    review_required_reason TEXT,
    -- SCH-W1.2-ORDER-STATE: decision-basis stamp; see world-copy comment above.
    q_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_venue_commands_position ON venue_commands(position_id);
CREATE INDEX IF NOT EXISTS idx_venue_commands_state ON venue_commands(state);
CREATE INDEX IF NOT EXISTS idx_venue_commands_decision ON venue_commands(decision_id);

-- venue_command_events + indexes (from src/state/db.py:init_schema executescript block)
CREATE TABLE IF NOT EXISTS venue_command_events (
    event_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT,
    state_after TEXT NOT NULL,
    UNIQUE (command_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_venue_command_events_command ON venue_command_events(command_id);
CREATE INDEX IF NOT EXISTS idx_venue_command_events_type ON venue_command_events(event_type);

-- settlement_day_observation_authority (OBS-AUTHORITY-FOUNDATION 2026-05-23).
-- Trade-class: the runtime opportunity_fact + decision evidence live on
-- zeus_trades.db, so the authority that joins to them is colocated there.
-- Canonical DDL source: _TRADE_CLASS_DDL in this file (src/state/db.py).
-- Created by init_schema_trade_only; NOT in architecture_kernel.sql.
CREATE TABLE IF NOT EXISTS settlement_day_observation_authority (
    authority_id TEXT PRIMARY KEY,
    city TEXT,
    target_date TEXT,
    temperature_metric TEXT
        CHECK (temperature_metric IS NULL OR temperature_metric IN ('high', 'low')),
    decision_time_utc TEXT,
    market_phase TEXT,
    source TEXT,
    station_id TEXT,
    observation_time_utc TEXT,
    first_sample_time_utc TEXT,
    last_sample_time_utc TEXT,
    high_so_far REAL,
    low_so_far REAL,
    current_temp REAL,
    sample_count INTEGER,
    coverage_status TEXT,
    freshness_status TEXT,
    local_date_matches_target INTEGER
        CHECK (local_date_matches_target IS NULL OR local_date_matches_target IN (0, 1)),
    source_authorized_for_settlement INTEGER
        CHECK (source_authorized_for_settlement IS NULL OR source_authorized_for_settlement IN (0, 1)),
    persisted_surface_available INTEGER
        CHECK (persisted_surface_available IS NULL OR persisted_surface_available IN (0, 1)),
    payload_json TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_settlement_day_obs_authority_city_target
    ON settlement_day_observation_authority(city, target_date, decision_time_utc);

-- outcome_fact (Repoint 2, fix/prearm-fill-exit-readiness 2026-06-03)
-- Canonical writer: harvester.py log_settlement_event → log_outcome_fact,
-- which receives trade_conn (zeus_trades.db). Copied from
-- architecture/2026_04_02_architecture_kernel.sql; schema_class corrected to
-- trade_class in architecture/db_table_ownership.yaml to match the live writer.
-- 18 rows confirmed on zeus_trades.db (0 on zeus-world.db) per probe-ownership.md.
CREATE TABLE IF NOT EXISTS outcome_fact (
    position_id TEXT PRIMARY KEY,
    strategy_key TEXT,
    entered_at TEXT,
    exited_at TEXT,
    settled_at TEXT,
    exit_reason TEXT,
    admin_exit_reason TEXT,
    decision_snapshot_id TEXT,
    pnl REAL,
    outcome INTEGER CHECK (outcome IN (0, 1)),
    hold_duration_hours REAL,
    monitor_count INTEGER,
    chain_corrections_count INTEGER
);
-- ============================================================================
-- PR-S4b completion (2026-07-01): 13 heritage trade tables created by
-- init_schema_pre_pr_s4b but never migrated into _TRADE_CLASS_DDL. Data lives
-- 100% on zeus_trades.db (probe: mph 622k, token_price_log 121k, etc.; world
-- ghost copies are 0-row, legacy_archived, drop-after-2026-08-09). Registry
-- converged them to trade_class; this wires their exact live DDL into fresh
-- trade init so init==registry==data. Idempotent CREATE ... IF NOT EXISTS.
-- ============================================================================
CREATE TABLE IF NOT EXISTS collateral_ledger_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pusd_balance_micro INTEGER NOT NULL,
  pusd_allowance_micro INTEGER NOT NULL,
  usdc_e_legacy_balance_micro INTEGER NOT NULL,
  ctf_token_balances_json TEXT NOT NULL,
  ctf_token_allowances_json TEXT NOT NULL,
  reserved_pusd_for_buys_micro INTEGER NOT NULL DEFAULT 0,
  reserved_tokens_for_sells_json TEXT NOT NULL DEFAULT '{}',
  captured_at TEXT NOT NULL,
  authority_tier TEXT NOT NULL CHECK (authority_tier IN ('CHAIN','VENUE','DEGRADED')),
  raw_balance_payload_hash TEXT
);
CREATE TABLE IF NOT EXISTS collateral_reservations (
  command_id TEXT PRIMARY KEY,
  reservation_type TEXT NOT NULL CHECK (reservation_type IN ('PUSD_BUY','CTF_SELL')),
  token_id TEXT,
  amount INTEGER NOT NULL CHECK (amount >= 0),
  created_at TEXT NOT NULL,
  released_at TEXT,
  release_reason TEXT,
  converted_amount INTEGER NOT NULL DEFAULT 0,
  CHECK (
    (reservation_type = 'PUSD_BUY' AND token_id IS NULL)
    OR (reservation_type = 'CTF_SELL' AND token_id IS NOT NULL)
  )
);
CREATE TABLE IF NOT EXISTS collateral_unsettled_proceeds (
  command_id TEXT PRIMARY KEY,
  direction TEXT NOT NULL CHECK (direction IN ('OUTGOING_DEDUCTION','INCOMING_PROCEEDS')),
  reservation_type TEXT NOT NULL CHECK (reservation_type IN ('PUSD_BUY','CTF_SELL')),
  token_id TEXT,
  amount_micro INTEGER NOT NULL CHECK (amount_micro >= 0),
  created_at TEXT NOT NULL,
  settled_at TEXT,
  settle_reason TEXT,
  CHECK (
    (reservation_type = 'PUSD_BUY' AND token_id IS NULL AND direction = 'OUTGOING_DEDUCTION')
    OR (reservation_type = 'CTF_SELL' AND token_id IS NOT NULL AND direction = 'INCOMING_PROCEEDS')
  )
);
CREATE INDEX IF NOT EXISTS idx_unsettled_open
  ON collateral_unsettled_proceeds (settled_at) WHERE settled_at IS NULL;
CREATE TRIGGER IF NOT EXISTS trg_reservations_no_overreserve
AFTER INSERT ON collateral_reservations
WHEN NEW.reservation_type = 'PUSD_BUY'
AND (
  (SELECT pusd_balance_micro FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1)
  - (SELECT COALESCE(SUM(amount),0) FROM collateral_reservations
     WHERE reservation_type='PUSD_BUY' AND released_at IS NULL)
  - (SELECT COALESCE(SUM(amount_micro),0) FROM collateral_unsettled_proceeds
     WHERE direction='OUTGOING_DEDUCTION' AND settled_at IS NULL)
) < 0
BEGIN
  SELECT RAISE(ABORT, 'COLLATERAL_OVERRESERVE');
END;
CREATE TABLE IF NOT EXISTS decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            artifact_json TEXT NOT NULL,
            timestamp TEXT NOT NULL
        , env TEXT NOT NULL DEFAULT 'live');
CREATE INDEX IF NOT EXISTS idx_decision_log_ts ON decision_log(timestamp);
CREATE TABLE IF NOT EXISTS exchange_reconcile_findings (
          finding_id TEXT PRIMARY KEY,
          kind TEXT NOT NULL CHECK (kind IN (
            'exchange_ghost_order','local_orphan_order','unrecorded_trade',
            'position_drift','heartbeat_suspected_cancel','cutover_wipe',
            'collateral_identity_mismatch'
          )),
          subject_id TEXT NOT NULL,
          context TEXT NOT NULL CHECK (context IN (
            'periodic','ws_gap','heartbeat_loss','cutover','operator'
          )),
          evidence_json TEXT NOT NULL,
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          resolved_at TEXT,
          resolution TEXT,
          resolved_by TEXT
        );
CREATE INDEX IF NOT EXISTS idx_findings_unresolved
          ON exchange_reconcile_findings (resolved_at)
          WHERE resolved_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_findings_unresolved_subject
          ON exchange_reconcile_findings (kind, subject_id, context)
          WHERE resolved_at IS NULL;
CREATE TABLE IF NOT EXISTS exit_mutex_holdings (
          mutex_key TEXT PRIMARY KEY,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
          acquired_at TEXT NOT NULL,
          released_at TEXT,
          release_reason TEXT
        );
CREATE TABLE IF NOT EXISTS market_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                token_id TEXT NOT NULL,
                price REAL NOT NULL CHECK (price >= 0.0 AND price <= 1.0),
                recorded_at TEXT NOT NULL,
                hours_since_open REAL,
                hours_to_resolution REAL,
                market_price_linkage TEXT NOT NULL DEFAULT 'price_only'
                    CHECK (market_price_linkage IN ('price_only', 'full')),
                source TEXT NOT NULL DEFAULT 'GAMMA_SCANNER',
                best_bid REAL CHECK (best_bid IS NULL OR (best_bid >= 0.0 AND best_bid <= 1.0)),
                best_ask REAL CHECK (best_ask IS NULL OR (best_ask >= 0.0 AND best_ask <= 1.0)),
                raw_orderbook_hash TEXT,
                snapshot_id TEXT,
                condition_id TEXT,
                UNIQUE(token_id, recorded_at)
            );
CREATE INDEX IF NOT EXISTS idx_market_price_history_condition_recorded
                ON market_price_history(condition_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_market_price_history_slug_recorded
                ON market_price_history(market_slug, recorded_at);
CREATE INDEX IF NOT EXISTS idx_market_price_history_snapshot
                ON market_price_history(snapshot_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_market_price_history_token_recorded
                ON market_price_history(token_id, recorded_at);
CREATE TABLE IF NOT EXISTS "opportunity_fact" (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    city TEXT,
    target_date TEXT,
    range_label TEXT,
    direction TEXT CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
    strategy_key TEXT,
    discovery_mode TEXT,
    entry_method TEXT,
    snapshot_id TEXT,
    p_raw REAL,
    p_cal REAL,
    p_market REAL,
    alpha REAL,
    best_edge REAL,
    ci_width REAL,
    rejection_stage TEXT,
    rejection_reason_json TEXT,
    availability_status TEXT CHECK (availability_status IN (
        'ok',
        'missing',
        'stale',
        'rate_limited',
        'unavailable',
        'chain_unavailable'
    )),
    should_trade INTEGER NOT NULL CHECK (should_trade IN (0, 1)),
    recorded_at TEXT NOT NULL
, observation_authority_id TEXT, day0_context_json TEXT);
CREATE TABLE IF NOT EXISTS provenance_envelope_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          subject_type TEXT NOT NULL CHECK (subject_type IN ('command','order','trade','lot','settlement','wrap_unwrap','heartbeat')),
          subject_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          payload_json TEXT,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          local_sequence INTEGER NOT NULL,
          UNIQUE (subject_type, subject_id, local_sequence)
        );
CREATE INDEX IF NOT EXISTS idx_envelope_events_subject ON provenance_envelope_events (subject_type, subject_id, observed_at);
CREATE TRIGGER IF NOT EXISTS provenance_envelope_events_no_delete
        BEFORE DELETE ON provenance_envelope_events
        BEGIN
          SELECT RAISE(ABORT, 'provenance_envelope_events is append-only');
        END;
CREATE TRIGGER IF NOT EXISTS provenance_envelope_events_no_update
        BEFORE UPDATE ON provenance_envelope_events
        BEGIN
          SELECT RAISE(ABORT, 'provenance_envelope_events is append-only');
        END;
CREATE TABLE IF NOT EXISTS risk_actions (
    action_id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type IN (
        'gate',
        'allocation_multiplier',
        'threshold_multiplier',
        'exit_only'
    )),
    value TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    effective_until TEXT,
    reason TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('riskguard', 'manual', 'system')),
    precedence INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'expired', 'revoked'))
);
CREATE TABLE IF NOT EXISTS "strategy_health" (
            strategy_key TEXT NOT NULL,
            as_of TEXT NOT NULL,
            open_exposure_usd REAL NOT NULL DEFAULT 0,
            settled_trades_30d INTEGER NOT NULL DEFAULT 0,
            realized_pnl_30d REAL NOT NULL DEFAULT 0,
            unrealized_pnl REAL NOT NULL DEFAULT 0,
            win_rate_30d REAL,
            brier_30d REAL,
            fill_rate_14d REAL,
            edge_trend_30d REAL,
            risk_level TEXT,
            execution_decay_flag INTEGER NOT NULL DEFAULT 0 CHECK (execution_decay_flag IN (0, 1)),
            edge_compression_flag INTEGER NOT NULL DEFAULT 0 CHECK (edge_compression_flag IN (0, 1)),
            PRIMARY KEY (strategy_key, as_of)
        );
CREATE TABLE IF NOT EXISTS token_price_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            price REAL NOT NULL,
            volume REAL,
            bid REAL,
            ask REAL,
            spread REAL,
            source_timestamp TEXT,
            timestamp TEXT NOT NULL
        );
CREATE INDEX IF NOT EXISTS idx_token_price_token
            ON token_price_log(token_id, timestamp);
CREATE TABLE IF NOT EXISTS token_suppression (
    token_id TEXT PRIMARY KEY,
    condition_id TEXT,
    suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
        'operator_quarantine_clear',
        'chain_only_quarantined',
        'settled_position'
    )),
    source_module TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_token_suppression_reason
    ON token_suppression(suppression_reason, updated_at);
CREATE TABLE IF NOT EXISTS token_suppression_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT NOT NULL,
    condition_id TEXT,
    suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
        'operator_quarantine_clear',
        'chain_only_quarantined',
        'settled_position'
    )),
    source_module TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    operation TEXT NOT NULL DEFAULT 'record' CHECK (operation IN ('record', 'migrated')),
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_suppression_history_id_time
    ON token_suppression_history(token_id, history_id DESC);
CREATE TRIGGER IF NOT EXISTS token_suppression_history_no_delete
BEFORE DELETE ON token_suppression_history
BEGIN
    SELECT RAISE(ABORT, 'token_suppression_history is append-only');
END;
CREATE TRIGGER IF NOT EXISTS token_suppression_history_no_update
BEFORE UPDATE ON token_suppression_history
BEGIN
    SELECT RAISE(ABORT, 'token_suppression_history is append-only');
END;
"""


def init_schema_trade_only(conn: sqlite3.Connection) -> None:
    """Create trade-class tables on state/zeus_trades.db. Idempotent.

    K1 split (2026-05-11): trade-class runtime tables plus the per-DB migration
    ledger own state/zeus_trades.db. World-class tables are created by
    init_schema_world_only on zeus-world.db; forecast-class tables by
    init_schema_forecasts.

    Post-K1 cold-boot contract:
        1. World conn: init_schema_world_only(world_conn) — 52 world-class tables
           + legacy_archived ghost shells.
        2. Trade conn: init_schema_trade_only(trade_conn) — trade DB tables ONLY.
           No world tables on trade.db.
        3. Forecasts conn (ingest daemon): init_schema_forecasts(forecasts_conn).

    Idempotent: all CREATEs use IF NOT EXISTS.

    Ghost tables (Case C — production zeus_trades.db):
        Pre-PR-S4b, src/main.py:1747 called init_schema(trade_conn) which is the
        world-schema constructor. This polluted zeus_trades.db with 66 world-class
        tables (including probability_trace_fact with 33k rows and availability_fact
        with 24k rows). These ghost tables are NOT dropped
        here — they are declared ``legacy_archived`` in architecture/db_table_ownership.yaml
        (db: trade, §4 Path B). The INV-37 writer fix (PR-S4b §3) redirects all future
        writes to zeus-world.db. Data migration is deferred per dispatch guidance
        ("DO NOT migrate data. DO NOT touch state/*.db.").

    PR-S4b (2026-05-18).
    """
    # Re-apply busy_timeout: executescript() resets the C-level busy handler.
    _busy_ms = int(os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))
    conn.execute(f"PRAGMA busy_timeout = {_busy_ms}")

    # Install custom SQLite scalar functions required by position_lots triggers.
    _install_connection_functions(conn)

    # Create the per-DB migration ledger from the migration framework's single
    # authority. The boot registry assertion treats it as a real trade DB table,
    # so fresh trade DBs must have it before assert_db_matches_registry(TRADE).
    from scripts.migrations import _ensure_ledger
    _ensure_ledger(conn, db_identity="trade")

    # Create the trade runtime tables (IF NOT EXISTS — idempotent).
    conn.executescript(_TRADE_CLASS_DDL)
    # Repoint 1 (fix/prearm-fill-exit-readiness, 2026-06-03): position_current
    # is trade_class (zeus_trades.db). _ensure_position_current_authority_columns
    # runs ALTER TABLE ADD COLUMN for every additive column (fill_authority,
    # chain_shares, chain_avg_price, chain_cost_basis_usd, chain_seen_at,
    # chain_absence_at, realized_pnl_usd, exit_price, settlement_price,
    # settled_at, exit_reason). On a FRESH DB the CREATE TABLE IF NOT EXISTS
    # above already includes all columns, so the function is a no-op. On a
    # LEGACY DB where position_current pre-dates any of these column additions
    # (e.g. zeus_trades.db with 101 live rows that pre-dated the W2 P&L commit),
    # this call migrates the live table instead of silently landing columns only
    # on the world-class ghost shell. Idempotent: skips columns that already exist.
    _ensure_position_current_authority_columns(conn)
    _migrate_trade_strategy_key_checks(conn)
    # Executable market substrate is live execution evidence. The market
    # discovery scheduler passes this same trade connection to snapshot_repo and
    # book_hash_transitions so snapshot rows and hash transitions commit together.
    init_snapshot_schema(conn)
    from src.state.schema.book_hash_transitions_schema import ensure_table as _ensure_book_hash_transitions_table
    _ensure_book_hash_transitions_table(conn)
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table as _ensure_execution_feasibility_evidence_table
    _ensure_execution_feasibility_evidence_table(conn)
    # W0.2 blind-window metric (docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
    # §1 A2): durable WS connect/disconnect/reconnect transition log. Trade DB owner.
    from src.state.schema.market_channel_connectivity_schema import ensure_table as _ensure_market_channel_connectivity_table
    _ensure_market_channel_connectivity_table(conn)
    # PR-E (2026-05-22): decision_integrity_quarantine lives on the trade DB.
    from src.state.schema.decision_integrity_quarantine_schema import ensure_table as _ensure_decision_integrity_quarantine_table
    _ensure_decision_integrity_quarantine_table(conn)
    try:
        conn.execute("ALTER TABLE trade_decisions ADD COLUMN env TEXT NOT NULL DEFAULT 'live';")
    except sqlite3.OperationalError:
        pass

    # H2_E2E (REAUDIT_0_1.md §2/§4): execution_fact gains the fill->posterior link.
    # Fresh DBs get it from _TRADE_CLASS_DDL above; legacy live DBs need this ALTER.
    # Nullable, no DEFAULT — preserves every existing execution_fact row unchanged
    # (observability only; never alters mainline/canonical fills). Duplicate-column
    # OperationalError is the expected fresh-DB no-op path.
    try:
        conn.execute("ALTER TABLE execution_fact ADD COLUMN posterior_id INTEGER;")
    except sqlite3.OperationalError:
        pass

    # SCH-W1.1-CAS-LEDGER (2026-07-02): converted_amount on collateral_reservations.
    # Fresh DBs get it from _TRADE_CLASS_DDL above; legacy live DBs need this ALTER.
    # NOT NULL DEFAULT 0 preserves every existing row (amount is immutable after
    # insert; converted_amount starts at 0, written exactly once at terminal
    # conversion). Duplicate-column OperationalError is the expected fresh-DB no-op.
    try:
        conn.execute(
            "ALTER TABLE collateral_reservations ADD COLUMN converted_amount INTEGER NOT NULL DEFAULT 0;"
        )
    except sqlite3.OperationalError:
        pass

    # settlement_commands + settlement_command_events
    # (DDL lives in src/execution/settlement_commands.py to keep the schema
    # co-located with the command implementation — same pattern as
    # SETTLEMENT_COMMAND_SCHEMA referenced in init_schema.)
    from src.execution.settlement_commands import (
        SETTLEMENT_COMMAND_SCHEMA,
        ensure_settlement_schema_ready,
    )
    conn.executescript(SETTLEMENT_COMMAND_SCHEMA)
    # ensure_settlement_schema_ready creates settlement_schema_migrations and
    # applies column migrations (idempotent). Must run after SETTLEMENT_COMMAND_SCHEMA
    # so the settlement_commands table exists for the ALTER TABLE steps.
    ensure_settlement_schema_ready(conn)

    # ctf_conversion_commands + ctf_conversion_command_events (W2.4, 2026-07-02)
    # (DDL lives in src/execution/ctf_conversion_commands.py to keep the schema
    # co-located with the command implementation — same pattern as
    # SETTLEMENT_COMMAND_SCHEMA above.)
    from src.execution.ctf_conversion_commands import CTF_CONVERSION_COMMAND_SCHEMA
    conn.executescript(CTF_CONVERSION_COMMAND_SCHEMA)

    # Re-apply busy_timeout: each executescript() resets the C-level handler.
    conn.execute(f"PRAGMA busy_timeout = {_busy_ms}")

    # Verify all registry-owned trade DB tables were actually created — guards
    # against silent DDL drift between _TRADE_CLASS_TABLES, the migration ledger,
    # _TRADE_CLASS_DDL, and SETTLEMENT_COMMAND_SCHEMA. Uses subset (not equality)
    # because Path B
    # leaves legacy_archived ghost tables on zeus_trades.db.
    _actual_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    _missing = _TRADE_CLASS_TABLES - _actual_tables
    if _missing:
        raise RuntimeError(
            f"init_schema_trade_only: DDL did not create expected trade-class tables: {sorted(_missing)}"
        )

    conn.commit()

    # NOTE: The 66 non-trade-class tables that pre-PR-S4b init_schema(trade_conn)
    # created on zeus_trades.db (including probability_trace_fact with 33k rows
    # and availability_fact with 24k rows) are
    # declared as legacy_archived in architecture/db_table_ownership.yaml (db: trade)
    # per PR-S4b §4 (Path B). They are NOT dropped here — INV-37 writer fix
    # (PR-S4b §3) redirects future writes to zeus-world.db. Data migration deferred
    # per dispatch: "DO NOT migrate data. DO NOT touch state/*.db."


_FORECASTS_LIVE_REQUIRED_INDEXES: frozenset[str] = frozenset(
    {
        "idx_forecast_posteriors_live_family_cycle",
        "idx_raw_model_forecasts_endpoint_family_cycle_members",
    }
)


def assert_schema_current_forecasts(conn: sqlite3.Connection) -> None:
    """Assert the forecasts DB has live-required schema surfaces.

    B2 (2026-05-28) removed the old schema-version counter; this assertion is the
    lightweight runtime guard that prevents read-only live boot from declaring a
    partially migrated forecasts DB "ready". Full DDL repair remains owned by
    ``init_schema_forecasts`` on the forecast ingest daemon.
    """

    indexes = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    missing_indexes = sorted(_FORECASTS_LIVE_REQUIRED_INDEXES - indexes)
    if missing_indexes:
        raise RuntimeError(
            "forecasts DB missing live-required indexes: "
            f"{missing_indexes}; run init_schema_forecasts before live trading"
        )

    for table in ("forecast_posteriors", "raw_model_forecasts"):
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone() is None:
            continue
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "trade_authority_status" in columns:
            raise RuntimeError(
                f"forecasts DB table {table} still has retired trade_authority_status column"
            )


_CALIBRATION_DECISION_GROUP_DDL = """
CREATE TABLE calibration_decision_group (
    group_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    forecast_available_at TEXT NOT NULL,
    cluster TEXT NOT NULL,
    season TEXT NOT NULL,
    lead_days REAL NOT NULL,
    settlement_value REAL,
    winning_range_label TEXT,
    bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1)),
    n_pair_rows INTEGER NOT NULL,
    n_positive_rows INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
)
"""

_CALIBRATION_DECISION_GROUP_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_calibration_decision_group_bucket
ON calibration_decision_group(cluster, season, lead_days)
"""


def _ensure_calibration_decision_group_lead_key(conn: sqlite3.Connection) -> None:
    """Migrate calibration groups if the legacy unique key lacks lead_days."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'calibration_decision_group'"
    ).fetchone()
    if row is None:
        return

    needs_migration = False
    for idx in conn.execute("PRAGMA index_list(calibration_decision_group)").fetchall():
        is_unique = bool(idx[2])
        if not is_unique:
            continue
        idx_name = idx[1]
        cols = [
            col[2]
            for col in conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
        ]
        if cols in (
            ["city", "target_date", "forecast_available_at"],
            ["city", "target_date", "forecast_available_at", "lead_days"],
        ):
            needs_migration = True
    if not needs_migration:
        return

    required_columns = {
        "group_id",
        "city",
        "target_date",
        "forecast_available_at",
        "cluster",
        "season",
        "lead_days",
        "settlement_value",
        "winning_range_label",
        "bias_corrected",
        "n_pair_rows",
        "n_positive_rows",
        "recorded_at",
    }
    existing_columns = {
        col[1] for col in conn.execute("PRAGMA table_info(calibration_decision_group)")
    }
    missing = sorted(required_columns - existing_columns)
    n_existing = conn.execute(
        "SELECT COUNT(*) FROM calibration_decision_group"
    ).fetchone()[0]
    if missing and n_existing:
        raise sqlite3.OperationalError(
            "Cannot migrate calibration_decision_group lead_days key: "
            f"non-empty legacy table is missing required columns {missing}"
        )
    if missing:
        backup_name = "calibration_decision_group__missing_cols_backup"
        logger.warning(
            f"Migrating empty calibration_decision_group schema to add {missing}. "
            f"Backing up existing schema to {backup_name} before rebuilding."
        )
        conn.execute(f"DROP TABLE IF EXISTS {backup_name}")
        conn.execute(f"ALTER TABLE calibration_decision_group RENAME TO {backup_name}")
        conn.execute(_CALIBRATION_DECISION_GROUP_DDL)
        conn.execute(_CALIBRATION_DECISION_GROUP_INDEX_DDL)
        return

    legacy_name = "calibration_decision_group__legacy_lead_key"
    conn.execute("SAVEPOINT calibration_decision_group_lead_key_migration")
    try:
        legacy_count = n_existing
        conn.execute(f"DROP TABLE IF EXISTS {legacy_name}")
        conn.execute(f"ALTER TABLE calibration_decision_group RENAME TO {legacy_name}")
        conn.execute(_CALIBRATION_DECISION_GROUP_DDL)
        conn.execute(
            f"""
            INSERT INTO calibration_decision_group (
                group_id,
                city,
                target_date,
                forecast_available_at,
                cluster,
                season,
                lead_days,
                settlement_value,
                winning_range_label,
                bias_corrected,
                n_pair_rows,
                n_positive_rows,
                recorded_at
            )
            SELECT
                group_id,
                city,
                target_date,
                forecast_available_at,
                cluster,
                season,
                lead_days,
                settlement_value,
                winning_range_label,
                bias_corrected,
                n_pair_rows,
                n_positive_rows,
                recorded_at
            FROM {legacy_name}
            """
        )
        conn.execute(_CALIBRATION_DECISION_GROUP_INDEX_DDL)
        new_count = conn.execute(
            "SELECT COUNT(*) FROM calibration_decision_group"
        ).fetchone()[0]
        if new_count != legacy_count:
            raise sqlite3.IntegrityError(
                "calibration_decision_group migration row-count mismatch: "
                f"{legacy_count} legacy rows, {new_count} copied rows"
            )
        conn.execute(f"DROP TABLE {legacy_name}")
        conn.execute("RELEASE SAVEPOINT calibration_decision_group_lead_key_migration")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT calibration_decision_group_lead_key_migration")
        conn.execute("RELEASE SAVEPOINT calibration_decision_group_lead_key_migration")
        raise


def _ensure_runtime_bootstrap_support_tables(conn: sqlite3.Connection) -> None:
    """Apply canonical architecture kernel schema."""
    apply_architecture_kernel_schema(conn)


def init_backtest_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Create derived backtest/reporting tables. Idempotent."""
    own_conn = conn is None
    if own_conn:
        conn = get_backtest_connection()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id TEXT PRIMARY KEY,
            lane TEXT NOT NULL CHECK (
                lane IN ('wu_settlement_sweep', 'trade_history_audit', 'selection_coverage')
            ),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            authority_scope TEXT NOT NULL CHECK (
                authority_scope = 'offline_no_promotion'
            ),
            config_json TEXT NOT NULL,
            summary_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backtest_outcome_comparison (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            lane TEXT NOT NULL CHECK (
                lane IN ('wu_settlement_sweep', 'trade_history_audit', 'selection_coverage')
            ),
            subject_id TEXT NOT NULL,
            subject_kind TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT,
            settlement_value REAL,
            settlement_unit TEXT,
            derived_wu_outcome INTEGER,
            actual_trade_outcome INTEGER,
            actual_pnl REAL,
            truth_source TEXT NOT NULL,
            divergence_status TEXT NOT NULL CHECK (
                divergence_status IN (
                    'not_applicable',
                    'match',
                    'wu_win_trade_loss',
                    'wu_loss_trade_win',
                    'trade_unresolved',
                    'wu_missing',
                    'bin_unparseable',
                    'ambiguous_subject',
                    'orphan_trade_decision',
                    'scored',
                    'no_snapshot',
                    'no_day0_nowcast_excluded',
                    'invalid_p_raw_json',
                    'empty_p_raw',
                    'label_count_mismatch',
                    'no_clob_best_bid',
                    'fdr_scan_failed',
                    'no_hypotheses'
                )
            ),
            decision_reference_source TEXT,
            forecast_reference_id TEXT,
            evidence_json TEXT NOT NULL,
            missing_reason_json TEXT NOT NULL,
            authority_scope TEXT NOT NULL DEFAULT 'offline_no_promotion'
                CHECK (authority_scope = 'offline_no_promotion'),
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_backtest_outcome_lane_city_date
            ON backtest_outcome_comparison(lane, city, target_date);
        CREATE INDEX IF NOT EXISTS idx_backtest_outcome_subject
            ON backtest_outcome_comparison(subject_id);
        CREATE INDEX IF NOT EXISTS idx_backtest_outcome_divergence
            ON backtest_outcome_comparison(divergence_status);
        CREATE INDEX IF NOT EXISTS idx_backtest_outcome_run
            ON backtest_outcome_comparison(run_id);

        -- PR E: replay/backtest output tables
        -- promotion_authority defaults to 0 on replay_subjects and replay_skill_results;
        -- these rows are diagnostic-only until explicit promotion gates are wired.

        CREATE TABLE IF NOT EXISTS replay_runs (
            run_id TEXT PRIMARY KEY,
            purpose TEXT,
            started_at TEXT,
            completed_at TEXT,
            status TEXT,
            code_version TEXT,
            schema_fingerprint TEXT,
            date_range_start TEXT,
            date_range_end TEXT,
            temperature_metric_scope TEXT,
            strategy_scope_json TEXT,
            authority_envelope_json TEXT,
            limitations_json TEXT
        );

        CREATE TABLE IF NOT EXISTS replay_subjects (
            replay_subject_id TEXT PRIMARY KEY,
            run_id TEXT,
            purpose TEXT,
            city TEXT,
            target_local_date TEXT,
            temperature_metric TEXT,
            forecast_object_id TEXT,
            settlement_object_id TEXT,
            outcome_set_id TEXT,
            bin_grid_id TEXT,
            decision_snapshot_id TEXT,
            decision_event_id TEXT,
            market_slug TEXT,
            condition_id TEXT,
            strategy_key TEXT,
            decision_time TEXT,
            point_in_time_provenance TEXT,
            promotion_authority INTEGER NOT NULL DEFAULT 0,  -- offline_no_promotion until gates wired
            learning_eligible INTEGER
        );

        CREATE TABLE IF NOT EXISTS forecast_probability_vectors (
            forecast_object_id TEXT PRIMARY KEY,
            replay_subject_id TEXT,
            dataset_id TEXT,
            source_id TEXT,
            source_run_id TEXT,
            cycle TEXT,
            lead_hours REAL,
            lead_bucket TEXT,
            bin_grid_id TEXT,
            bin_schema_id TEXT,
            ordered_bin_ids_json TEXT,
            ordered_labels_json TEXT,
            p_raw_json TEXT,
            p_cal_json TEXT,
            p_market_json TEXT,
            p_posterior_json TEXT,
            normalization_status TEXT,
            available_at TEXT,
            fetch_time TEXT,
            authority TEXT,
            provenance_json TEXT
        );

        CREATE TABLE IF NOT EXISTS settlement_resolution_truth (
            settlement_object_id TEXT PRIMARY KEY,
            city TEXT,
            target_local_date TEXT,
            temperature_metric TEXT,
            settlement_value REAL,
            settlement_unit TEXT,
            settlement_rounding_policy TEXT,
            settlement_source TEXT,
            settled_at TEXT,
            market_slug TEXT,
            condition_id TEXT,
            winning_bin_id_derived TEXT,
            stored_winning_bin_evidence TEXT,
            winning_asset_id TEXT,
            winning_token_id TEXT,
            resolution_status TEXT,
            authority TEXT,
            evidence_class TEXT,
            learning_eligible INTEGER,
            promotion_eligible INTEGER,
            provenance_json TEXT
        );

        CREATE TABLE IF NOT EXISTS replay_skill_results (
            skill_result_id TEXT PRIMARY KEY,
            run_id TEXT,
            replay_subject_id TEXT,
            forecast_object_id TEXT,
            settlement_object_id TEXT,
            p_vector_json TEXT,
            y_vector_json TEXT,
            winner_bin_id TEXT,
            p_winner REAL,
            categorical_log_loss REAL,
            multiclass_brier REAL,
            ranked_probability_score REAL,
            top1_hit INTEGER,
            top3_hit INTEGER,
            winner_rank INTEGER,
            reciprocal_rank REAL,
            group_integrity_status TEXT,
            group_exclusion_reason TEXT,
            promotion_authority INTEGER NOT NULL DEFAULT 0,  -- offline_no_promotion until gates wired
            learning_eligible INTEGER,
            limitations_json TEXT
        );
    """)
    conn.commit()
    if own_conn:
        conn.close()



def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_has_unique_key(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> bool:
    """Return whether *table* has a UNIQUE index exactly matching *columns*."""
    for index_row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if not bool(index_row[2]):
            continue
        index_name = index_row[1]
        index_columns = tuple(
            column_row[2]
            for column_row in conn.execute(f"PRAGMA index_info({index_name})").fetchall()
        )
        if index_columns == columns:
            return True
    return False


def _view_exists(conn: sqlite3.Connection, view: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?",
        (view,),
    ).fetchone()
    return row is not None


def _table_or_view_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Return True if `name` exists as either a TABLE or a VIEW."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def list_sqlite_tables_and_views_read_only(db_path: str | Path) -> tuple[str, ...]:
    """Return table/view names from a SQLite DB using a read-only URI."""

    path = Path(db_path)
    if not path.exists():
        return ()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return ()
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    except sqlite3.Error:
        return ()
    finally:
        conn.close()
    return tuple(str(row[0]) for row in rows)


_FORWARD_MARKET_EVENT_COLUMNS = (
    "market_slug",
    "city",
    "target_date",
    "temperature_metric",
    "condition_id",
    "token_id",
    "range_label",
    "range_low",
    "range_high",
    "outcome",
    "created_at",
    "recorded_at",
)
_FORWARD_PRICE_HISTORY_COLUMNS = (
    "market_slug",
    "token_id",
    "price",
    "recorded_at",
    "hours_since_open",
    "hours_to_resolution",
)
_FULL_LINKAGE_PRICE_HISTORY_COLUMNS = (
    "market_slug",
    "token_id",
    "price",
    "recorded_at",
    "hours_since_open",
    "hours_to_resolution",
    "market_price_linkage",
    "source",
    "best_bid",
    "best_ask",
    "raw_orderbook_hash",
    "snapshot_id",
    "condition_id",
)
_FULL_LINKAGE_PRICE_REQUIRED_COLUMNS = (
    "market_price_linkage",
    "source",
    "best_bid",
    "best_ask",
    "raw_orderbook_hash",
    "snapshot_id",
    "condition_id",
)
_FORWARD_MARKET_REQUIRED_TABLES = (
    "market_events",
    "market_price_history",
)
_MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES = ("market_topology_state",)
_MARKET_TOPOLOGY_STATE_REQUIRED_COLUMNS = (
    "topology_id",
    "scope_key",
    "market_family",
    "event_id",
    "condition_id",
    "question_id",
    "city_id",
    "city_timezone",
    "target_local_date",
    "temperature_metric",
    "physical_quantity",
    "observation_field",
    "data_version",
    "token_ids_json",
    "bin_topology_hash",
    "gamma_captured_at",
    "gamma_updated_at",
    "source_contract_status",
    "source_contract_reason",
    "authority_status",
    "status",
    "expires_at",
    "provenance_json",
    "recorded_at",
)
_SOURCE_CONTRACT_AUDIT_TABLES = ("source_contract_audit_events",)
_SOURCE_CONTRACT_AUDIT_REQUIRED_COLUMNS = (
    "audit_id",
    "checked_at_utc",
    "scan_authority",
    "report_status",
    "severity",
    "event_id",
    "slug",
    "title",
    "city",
    "target_date",
    "temperature_metric",
    "source_contract_status",
    "source_contract_reason",
    "configured_source_family",
    "configured_station_id",
    "observed_source_family",
    "observed_station_id",
    "resolution_sources_json",
    "source_contract_json",
    "payload_hash",
    "created_at",
)
_SOURCE_CONTRACT_AUDIT_AUTHORITIES = frozenset({
    "VERIFIED",
    "FIXTURE",
    "STALE_CACHE",
    "FETCH_FAILED_NO_CACHE",
    "KEYWORD_DISCOVERY_UNVERIFIED",
    "NEVER_FETCHED",
})
_SOURCE_CONTRACT_AUDIT_SEVERITIES = frozenset({
    "OK",
    "WARN",
    "ALERT",
    "DATA_UNAVAILABLE",
})
_SOURCE_CONTRACT_AUDIT_REPORT_STATUSES = _SOURCE_CONTRACT_AUDIT_SEVERITIES
_SOURCE_CONTRACT_AUDIT_STATUSES = frozenset({
    "MATCH",
    "MISSING",
    "AMBIGUOUS",
    "MISMATCH",
    "UNSUPPORTED",
    "UNKNOWN",
    "QUARANTINED",
})
_SETTLEMENT_V2_COLUMNS = (
    "city",
    "target_date",
    "temperature_metric",
    "market_slug",
    "winning_bin",
    "settlement_value",
    "settlement_source",
    "settled_at",
    "authority",
    "provenance_json",
    "recorded_at",
    "settlement_unit",
)
_MARKET_EVENT_OUTCOME_VALUES = frozenset({"YES", "NO"})
_MARKET_EVENT_OUTCOME_UPDATE_SQL = """
    UPDATE market_events
    SET outcome = ?
    WHERE market_slug = ?
      AND condition_id = ?
      AND token_id = ?
      AND city = ?
      AND target_date = ?
      AND temperature_metric = ?
"""


def _forward_clean_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _forward_city_name(value) -> str | None:
    name = getattr(value, "name", value)
    return _forward_clean_str(name)


def _forward_metric(value) -> str | None:
    metric = _forward_clean_str(value)
    if metric is None:
        return None
    metric = metric.lower()
    return metric if metric in {"high", "low"} else None


def _forward_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _forward_price(value) -> float | None:
    price = _forward_float(value)
    if price is None or not 0.0 <= price <= 1.0:
        return None
    return price


def _forward_values_equal(left, right) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    if isinstance(right, float):
        try:
            return abs(float(left) - right) < 1e-12
        except (TypeError, ValueError):
            return False
    return str(left) == str(right)


def _forward_existing_matches(existing, expected: dict, *, ignore: set[str] | None = None) -> bool:
    ignored = ignore or set()
    for key, value in expected.items():
        if key in ignored:
            continue
        if not _forward_values_equal(existing[key], value):
            return False
    return True


def _forward_existing_outcome_is_resolved(existing: dict) -> bool:
    outcome = _forward_clean_str(existing.get("outcome"))
    if outcome is None:
        return False
    range_label = _forward_clean_str(existing.get("range_label"))
    return outcome != range_label


def _insert_forward_market_event(conn: sqlite3.Connection, values: dict) -> str:
    existing = conn.execute(
        """
        SELECT market_slug, city, target_date, temperature_metric, condition_id,
               token_id, range_label, range_low, range_high, outcome, created_at,
               recorded_at
        FROM market_events
        WHERE market_slug = ? AND condition_id = ?
        """,
        (values["market_slug"], values["condition_id"]),
    ).fetchone()
    if existing is not None:
        existing_values = dict(zip(_FORWARD_MARKET_EVENT_COLUMNS, tuple(existing)))
        if _forward_existing_outcome_is_resolved(existing_values):
            return "resolved_existing"
        ignored = {"recorded_at", "outcome"}
        if _forward_clean_str(existing_values.get("created_at")) is None:
            ignored.add("created_at")
        if _forward_existing_matches(existing_values, values, ignore=ignored):
            return "unchanged"
        return "conflict"

    conn.execute(
        """
        INSERT INTO market_events (
            market_slug, city, target_date, temperature_metric, condition_id,
            token_id, range_label, range_low, range_high, outcome, created_at,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(values[column] for column in _FORWARD_MARKET_EVENT_COLUMNS),
    )
    return "inserted"


def _insert_forward_price_history(conn: sqlite3.Connection, values: dict) -> str:
    # Owner-routed (2026-07-01): market_price_history is trade-owned. This forward-substrate writer runs on a
    # forecasts-rooted conn, so it cannot reach trade -> SKIP (never write the forecasts ghost). When called on
    # a trade-reachable conn it lands in the owner. See site-8 decision in the atlas §6C.
    from src.state.owner_routed_write import owner_write_target
    _mph = owner_write_target(conn, "market_price_history")
    if _mph is None:
        return "skipped_wrong_db"
    existing = conn.execute(
        f"""
        SELECT market_slug, token_id, price, recorded_at, hours_since_open,
               hours_to_resolution
        FROM {_mph}
        WHERE token_id = ? AND recorded_at = ?
        """,
        (values["token_id"], values["recorded_at"]),
    ).fetchone()
    if existing is not None:
        existing_values = dict(zip(_FORWARD_PRICE_HISTORY_COLUMNS, tuple(existing)))
        if _forward_existing_matches(existing_values, values):
            return "unchanged"
        return "conflict"

    conn.execute(
        f"""
        INSERT INTO {_mph} (
            market_slug, token_id, price, recorded_at, hours_since_open,
            hours_to_resolution
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        tuple(values[column] for column in _FORWARD_PRICE_HISTORY_COLUMNS),
    )
    return "inserted"


def _insert_full_linkage_price_history(conn: sqlite3.Connection, values: dict) -> str:
    # Owner-routed (2026-07-01): market_price_history is trade-owned. This executable-snapshot-linkage writer
    # runs on a trade-rooted conn -> owner_write_target returns the bare name (no-op); a wrong-DB conn SKIPs.
    from src.state.owner_routed_write import owner_write_target
    _mph = owner_write_target(conn, "market_price_history")
    if _mph is None:
        return "skipped_wrong_db"
    existing = conn.execute(
        f"""
        SELECT market_slug, token_id, price, recorded_at, hours_since_open,
               hours_to_resolution, market_price_linkage, source, best_bid,
               best_ask, raw_orderbook_hash, snapshot_id, condition_id
        FROM {_mph}
        WHERE token_id = ? AND recorded_at = ?
        """,
        (values["token_id"], values["recorded_at"]),
    ).fetchone()
    if existing is not None:
        existing_values = dict(zip(_FULL_LINKAGE_PRICE_HISTORY_COLUMNS, tuple(existing)))
        if _forward_existing_matches(existing_values, values):
            return "unchanged"
        return "conflict"

    conn.execute(
        f"""
        INSERT INTO {_mph} (
            market_slug, token_id, price, recorded_at, hours_since_open,
            hours_to_resolution, market_price_linkage, source, best_bid,
            best_ask, raw_orderbook_hash, snapshot_id, condition_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(values[column] for column in _FULL_LINKAGE_PRICE_HISTORY_COLUMNS),
    )
    return "inserted"


def _mid_price(best_bid: float, best_ask: float) -> float | None:
    if best_bid > best_ask:
        return None
    return (best_bid + best_ask) / 2.0


def log_executable_snapshot_market_price_linkage(
    conn: sqlite3.Connection | None,
    *,
    snapshot_id: str,
    source: str = "CLOB_ORDERBOOK",
    recorded_at: str | None = None,
) -> dict:
    """Persist full CLOB top-of-book linkage from an executable snapshot.

    The scanner writer records price-only Gamma substrate. This helper records
    the CLOB orderbook evidence already captured for an executable entry
    snapshot. It never opens a default DB and never commits; callers own the
    transaction boundary.
    """
    table = "market_price_history"
    snapshot_table = "executable_market_snapshots"
    if conn is None:
        return {"status": "skipped_no_connection", "tables": (table, snapshot_table)}

    snapshot_id_value = _forward_clean_str(snapshot_id)
    if snapshot_id_value is None:
        return {"status": "refused_missing_snapshot_id", "tables": (table, snapshot_table)}

    missing_tables = [
        required
        for required in (table, snapshot_table)
        if not _table_exists(conn, required)
    ]
    if missing_tables:
        return {
            "status": "skipped_missing_tables",
            "tables": (table, snapshot_table),
            "missing_tables": tuple(missing_tables),
        }

    missing_columns = tuple(
        sorted(set(_FULL_LINKAGE_PRICE_REQUIRED_COLUMNS) - _table_columns(conn, table))
    )
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_columns": missing_columns,
        }

    saved_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT snapshot_id, event_slug, condition_id, selected_outcome_token_id,
                   orderbook_top_bid, orderbook_top_ask, raw_orderbook_hash,
                   captured_at
            FROM executable_market_snapshots
            WHERE snapshot_id = ?
            """,
            (snapshot_id_value,),
        ).fetchone()
    finally:
        conn.row_factory = saved_factory
    if row is None:
        return {"status": "refused_missing_snapshot", "snapshot_id": snapshot_id_value}

    market_slug = _forward_clean_str(row["event_slug"])
    token_id = _forward_clean_str(row["selected_outcome_token_id"])
    condition_id = _forward_clean_str(row["condition_id"])
    best_bid = _forward_price(row["orderbook_top_bid"])
    best_ask = _forward_price(row["orderbook_top_ask"])
    raw_orderbook_hash = _forward_clean_str(row["raw_orderbook_hash"])
    recorded_at_value = _forward_clean_str(recorded_at) or _forward_clean_str(row["captured_at"])
    source_value = _forward_clean_str(source)
    if not (
        market_slug
        and token_id
        and condition_id
        and best_bid is not None
        and best_ask is not None
        and raw_orderbook_hash
        and recorded_at_value
        and source_value
    ):
        return {"status": "refused_missing_snapshot_facts", "snapshot_id": snapshot_id_value}

    price = _mid_price(best_bid, best_ask)
    if price is None:
        return {
            "status": "refused_crossed_orderbook",
            "snapshot_id": snapshot_id_value,
            "best_bid": best_bid,
            "best_ask": best_ask,
        }

    values = {
        "market_slug": market_slug,
        "token_id": token_id,
        "price": price,
        "recorded_at": recorded_at_value,
        "hours_since_open": None,
        "hours_to_resolution": None,
        "market_price_linkage": "full",
        "source": source_value,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "raw_orderbook_hash": raw_orderbook_hash,
        "snapshot_id": snapshot_id_value,
        "condition_id": condition_id,
    }
    result = _insert_full_linkage_price_history(conn, values)
    return {
        "status": result,
        "table": table,
        "snapshot_id": snapshot_id_value,
        "token_id": token_id,
        "recorded_at": recorded_at_value,
    }


def capture_intraday_orderbook_depth_from_snapshots(
    conn: sqlite3.Connection | None,
    *,
    condition_ids: "Iterable[str]",
    recorded_at: str,
    as_of: str | None = None,
) -> dict:
    """ThePath P1 ITEM 3 (2026-06-07): additive, fail-soft intraday depth capture.

    The intraday Gamma scanner writes mid-only ``market_price_history`` rows
    (best_bid/best_ask/raw_orderbook_hash are 100% NULL on the price_only plane).
    This helper closes the order-book depth gap for the fill model WITHOUT adding
    any new high-frequency external poll: it taps the ``executable_market_snapshots``
    rows the executor/scanner has ALREADY captured (which carry the full CLOB
    ladder + top-of-book + raw_orderbook_hash), selects the most-recent snapshot
    at-or-before ``as_of`` (default ``recorded_at``) per condition_id, and writes a
    ``market_price_linkage='full'`` row with best_bid/best_ask/raw_orderbook_hash.

    Design properties (iron rules):
      - ADDITIVE: only writes new full-linkage rows; never updates/rewrites the
        existing mid-only price_only rows. Columns already exist (no schema change).
      - FAIL-SOFT: every per-condition failure (no snapshot, crossed book, missing
        facts) is recorded as a typed counter and skipped — this function NEVER
        raises and NEVER aborts the caller's cycle. A missing EMS table degrades
        to a no-op status.
      - NO NEW POLL: reads only already-persisted EMS rows; performs no network I/O.
      - ANTI-LOOKAHEAD: ``captured_at <= as_of`` strictly (uses already-captured
        snapshots only); never a future snapshot.
      - Caller owns the transaction boundary (no commit, no default-DB open),
        mirroring ``log_executable_snapshot_market_price_linkage``.

    Returns a status dict with per-condition outcome counters.
    """
    table = "market_price_history"
    snapshot_table = "executable_market_snapshots"
    counts = {
        "rows_inserted": 0,
        "rows_unchanged": 0,
        "rows_conflicted": 0,
        "skipped_no_snapshot": 0,
        "skipped_crossed_book": 0,
        "skipped_missing_facts": 0,
        "conditions_seen": 0,
    }
    if conn is None:
        return {"status": "skipped_no_connection", "tables": (table, snapshot_table), **counts}

    recorded_at_value = _forward_clean_str(recorded_at)
    if recorded_at_value is None:
        return {"status": "refused_missing_recorded_at", "tables": (table, snapshot_table), **counts}
    as_of_value = _forward_clean_str(as_of) or recorded_at_value

    # Fail-soft schema guard: if EMS or mph is absent (or mph lacks the depth
    # columns), degrade to a no-op rather than raising.
    if not _table_exists(conn, snapshot_table) or not _table_exists(conn, table):
        return {"status": "skipped_missing_tables", "tables": (table, snapshot_table), **counts}
    missing_columns = tuple(
        sorted(set(_FULL_LINKAGE_PRICE_REQUIRED_COLUMNS) - _table_columns(conn, table))
    )
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_columns": missing_columns,
            **counts,
        }

    seen: set[str] = set()
    saved_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        for raw_condition in condition_ids or ():
            condition_id = _forward_clean_str(raw_condition)
            if condition_id is None or condition_id in seen:
                continue
            seen.add(condition_id)
            counts["conditions_seen"] += 1

            # Most-recent already-captured snapshot at-or-before as_of (anti-lookahead).
            try:
                row = conn.execute(
                    """
                    SELECT snapshot_id, event_slug, condition_id,
                           selected_outcome_token_id,
                           orderbook_top_bid, orderbook_top_ask, raw_orderbook_hash,
                           captured_at
                    FROM executable_market_snapshots
                    WHERE condition_id = ? AND captured_at <= ?
                    ORDER BY captured_at DESC
                    LIMIT 1
                    """,
                    (condition_id, as_of_value),
                ).fetchone()
            except sqlite3.OperationalError:
                # EMS query failed (e.g. schema mismatch) — fail soft for this cond.
                counts["skipped_no_snapshot"] += 1
                continue
            if row is None:
                counts["skipped_no_snapshot"] += 1
                continue

            market_slug = _forward_clean_str(row["event_slug"])
            token_id = _forward_clean_str(row["selected_outcome_token_id"])
            best_bid = _forward_price(row["orderbook_top_bid"])
            best_ask = _forward_price(row["orderbook_top_ask"])
            raw_orderbook_hash = _forward_clean_str(row["raw_orderbook_hash"])
            snapshot_id_value = _forward_clean_str(row["snapshot_id"])
            if not (
                market_slug
                and token_id
                and best_bid is not None
                and best_ask is not None
                and raw_orderbook_hash
                and snapshot_id_value
            ):
                counts["skipped_missing_facts"] += 1
                continue

            price = _mid_price(best_bid, best_ask)
            if price is None:
                counts["skipped_crossed_book"] += 1
                continue

            values = {
                "market_slug": market_slug,
                "token_id": token_id,
                "price": price,
                # recorded_at is the SCAN time (the intraday cadence), not the
                # snapshot capture time — so the depth row aligns with the
                # mid-only row written by the same scan cycle. snapshot_id +
                # raw_orderbook_hash retain the provenance back to the EMS book.
                "recorded_at": recorded_at_value,
                "hours_since_open": None,
                "hours_to_resolution": None,
                "market_price_linkage": "full",
                "source": "CLOB_ORDERBOOK_EMS_TAP",
                "best_bid": best_bid,
                "best_ask": best_ask,
                "raw_orderbook_hash": raw_orderbook_hash,
                "snapshot_id": snapshot_id_value,
                "condition_id": condition_id,
            }
            try:
                result = _insert_full_linkage_price_history(conn, values)
            except sqlite3.Error:
                # UNIQUE(token_id, recorded_at) collision or any insert error:
                # fail soft, count as conflict, never abort the cycle.
                counts["rows_conflicted"] += 1
                continue
            if result == "inserted":
                counts["rows_inserted"] += 1
            elif result == "unchanged":
                counts["rows_unchanged"] += 1
            else:
                counts["rows_conflicted"] += 1
    finally:
        conn.row_factory = saved_factory

    status = "ok"
    if counts["rows_conflicted"]:
        status = "ok_with_conflicts"
    return {"status": status, "table": table, **counts}


def log_forward_market_substrate(
    *,
    markets: Iterable[dict],
    recorded_at: str,
    scan_authority: str,
    _db_path: "str | Path | None" = None,
) -> dict:
    """Persist Gamma scanner market identity and price observations.

    K1-A fix (2026-05-17): opens its own forecasts connection rather than
    accepting an opaque conn from callers. Callers that passed the cycle
    trades-rooted conn were silently writing market_events rows to
    zeus_trades.db (MAIN) instead of zeus-forecasts.db. Decision A2.

    _db_path: override for testing; defaults to ZEUS_FORECASTS_DB_PATH.
    """
    resolved_path = Path(_db_path) if _db_path is not None else ZEUS_FORECASTS_DB_PATH

    if str(scan_authority or "").strip().upper() != "VERIFIED":
        return {
            "status": "refused_degraded_authority",
            "tables": _FORWARD_MARKET_REQUIRED_TABLES,
            "scan_authority": scan_authority,
        }

    recorded_at_value = _forward_clean_str(recorded_at)
    if recorded_at_value is None:
        return {"status": "refused_missing_recorded_at", "tables": _FORWARD_MARKET_REQUIRED_TABLES}

    conn = sqlite3.connect(str(resolved_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        missing_tables = [
            table for table in _FORWARD_MARKET_REQUIRED_TABLES if not _table_exists(conn, table)
        ]
        if missing_tables:
            return {
                "status": "skipped_missing_tables",
                "tables": _FORWARD_MARKET_REQUIRED_TABLES,
                "missing_tables": tuple(missing_tables),
            }

        required_columns = {
            "market_events": set(_FORWARD_MARKET_EVENT_COLUMNS),
            "market_price_history": set(_FORWARD_PRICE_HISTORY_COLUMNS),
        }
        missing_columns = {
            table: tuple(sorted(required_columns[table] - _table_columns(conn, table)))
            for table in required_columns
        }
        missing_columns = {table: columns for table, columns in missing_columns.items() if columns}
        if missing_columns:
            return {
                "status": "skipped_invalid_schema",
                "tables": _FORWARD_MARKET_REQUIRED_TABLES,
                "missing_columns": missing_columns,
            }

        counts = {
            "market_events_inserted": 0,
            "market_events_unchanged": 0,
            "market_events_conflicted": 0,
            "price_rows_inserted": 0,
            "price_rows_unchanged": 0,
            "price_rows_conflicted": 0,
            "markets_skipped_missing_facts": 0,
            "outcomes_skipped_missing_facts": 0,
            "prices_skipped_missing_facts": 0,
            "outcomes_skipped_with_outcome_fact": 0,
        }

        for market in markets:
            if not isinstance(market, dict):
                counts["markets_skipped_missing_facts"] += 1
                continue
            market_slug = _forward_clean_str(market.get("slug"))
            city = _forward_city_name(market.get("city"))
            target_date = _forward_clean_str(market.get("target_date"))
            temperature_metric = _forward_metric(market.get("temperature_metric"))
            if not (market_slug and city and target_date and temperature_metric):
                counts["markets_skipped_missing_facts"] += 1
                continue

            hours_since_open = _forward_float(market.get("hours_since_open"))
            hours_to_resolution = _forward_float(market.get("hours_to_resolution"))

            for outcome in market.get("outcomes") or ():
                if not isinstance(outcome, dict):
                    counts["outcomes_skipped_missing_facts"] += 1
                    continue
                if _forward_clean_str(outcome.get("outcome")) is not None:
                    counts["outcomes_skipped_with_outcome_fact"] += 1
                    continue

                condition_id = _forward_clean_str(outcome.get("condition_id"))
                yes_token = _forward_clean_str(outcome.get("token_id"))
                range_label = _forward_clean_str(outcome.get("title"))
                range_low = _forward_float(outcome.get("range_low"))
                range_high = _forward_float(outcome.get("range_high"))
                if not (
                    condition_id
                    and yes_token
                    and range_label
                    and (range_low is not None or range_high is not None)
                ):
                    counts["outcomes_skipped_missing_facts"] += 1
                    continue

                event_values = {
                    "market_slug": market_slug,
                    "city": city,
                    "target_date": target_date,
                    "temperature_metric": temperature_metric,
                    "condition_id": condition_id,
                    "token_id": yes_token,
                    "range_label": range_label,
                    "range_low": range_low,
                    "range_high": range_high,
                    "outcome": None,
                    "created_at": _forward_clean_str(
                        market.get("created_at") or outcome.get("market_start_at")
                    ),
                    "recorded_at": recorded_at_value,
                }
                event_result = _insert_forward_market_event(conn, event_values)
                if event_result == "resolved_existing":
                    counts["outcomes_skipped_with_outcome_fact"] += 1
                    continue
                if event_result == "conflict":
                    counts["market_events_conflicted"] += 1
                    continue
                counts[f"market_events_{event_result}"] += 1

                for token_key, price_key in (("token_id", "price"), ("no_token_id", "no_price")):
                    token_id = _forward_clean_str(outcome.get(token_key))
                    price = _forward_price(outcome.get(price_key))
                    if token_id is None or price is None:
                        counts["prices_skipped_missing_facts"] += 1
                        continue
                    price_values = {
                        "market_slug": market_slug,
                        "token_id": token_id,
                        "price": price,
                        "recorded_at": recorded_at_value,
                        "hours_since_open": hours_since_open,
                        "hours_to_resolution": hours_to_resolution,
                    }
                    price_result = _insert_forward_price_history(conn, price_values)
                    price_key_name = "price_rows_conflicted" if price_result == "conflict" else f"price_rows_{price_result}"
                    counts[price_key_name] += 1

        conn.commit()
    finally:
        conn.close()

    status = "written"
    if counts["market_events_conflicted"] or counts["price_rows_conflicted"]:
        status = "written_with_conflicts"
    elif (
        counts["market_events_inserted"] == 0
        and counts["price_rows_inserted"] == 0
        and (counts["market_events_unchanged"] or counts["price_rows_unchanged"])
    ):
        status = "unchanged"
    elif counts["market_events_inserted"] == 0 and counts["price_rows_inserted"] == 0:
        status = "skipped_no_valid_rows"

    return {
        "status": status,
        "tables": _FORWARD_MARKET_REQUIRED_TABLES,
        **counts,
    }


def log_market_source_contract_topology_facts(
    conn: sqlite3.Connection | None,  # Deprecated: ignored; function opens its own world connection (INV-37 fix, wave-2)
    *,
    markets: Iterable[dict],
    recorded_at: str,
    scan_authority: str,
) -> dict:
    """Persist scanner source-contract proof into market_topology_state in zeus-world.db.

    INV-37 fix (wave-2, 2026-05-18): opens its own world connection rather than
    accepting an opaque conn from callers. Pre-fix, callers could pass a
    trades-rooted or forecasts-ATTACHed conn, causing writes to land in the wrong
    DB. The ``conn`` parameter is kept for backward compat but is no longer used.
    """
    if str(scan_authority or "").strip().upper() != "VERIFIED":
        return {
            "status": "refused_degraded_authority",
            "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES,
            "scan_authority": scan_authority,
        }

    recorded_at_value = _forward_clean_str(recorded_at)
    if recorded_at_value is None:
        return {"status": "refused_missing_recorded_at", "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES}

    _wconn = get_world_connection(write_class="live")
    try:
        missing_tables = [
            table for table in _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES if not _table_exists(_wconn, table)
        ]
        if missing_tables:
            return {
                "status": "skipped_missing_tables",
                "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES,
                "missing_tables": tuple(missing_tables),
            }

        missing_columns = tuple(
            sorted(set(_MARKET_TOPOLOGY_STATE_REQUIRED_COLUMNS) - _table_columns(_wconn, "market_topology_state"))
        )
        if missing_columns:
            return {
                "status": "skipped_invalid_schema",
                "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES,
                "missing_columns": {"market_topology_state": missing_columns},
            }

        counts = {
            "topology_rows_written": 0,
            "markets_skipped_missing_facts": 0,
            "markets_skipped_source_contract_status": 0,
            "outcomes_skipped_missing_facts": 0,
        }

        for market in markets:
            if not isinstance(market, dict):
                counts["markets_skipped_missing_facts"] += 1
                continue
            source_contract = market.get("source_contract") or {}
            if not isinstance(source_contract, dict):
                counts["markets_skipped_source_contract_status"] += 1
                continue
            source_contract_status = _forward_clean_str(source_contract.get("status"))
            if source_contract_status != "MATCH":
                counts["markets_skipped_source_contract_status"] += 1
                continue

            market_slug = _forward_clean_str(market.get("slug"))
            event_id = _forward_clean_str(market.get("event_id"))
            city_obj = market.get("city")
            city_name = _forward_city_name(city_obj)
            city_timezone = _forward_clean_str(
                market.get("city_timezone") or getattr(city_obj, "timezone", None)
            )
            target_date = _forward_clean_str(market.get("target_date"))
            temperature_metric = _forward_metric(market.get("temperature_metric"))
            if not (market_slug and city_name and target_date and temperature_metric):
                counts["markets_skipped_missing_facts"] += 1
                continue

            data_version = _forward_clean_str(market.get("data_version")) or "gamma_source_contract_v1"
            observation_field = (
                "daily_max_temperature" if temperature_metric == "high" else "daily_min_temperature"
            )
            resolution_sources = list(source_contract.get("resolution_sources") or market.get("resolution_sources") or [])

            for outcome in market.get("outcomes") or ():
                if not isinstance(outcome, dict):
                    counts["outcomes_skipped_missing_facts"] += 1
                    continue
                condition_id = _forward_clean_str(outcome.get("condition_id"))
                if condition_id is None:
                    counts["outcomes_skipped_missing_facts"] += 1
                    continue
                question_id = _forward_clean_str(outcome.get("question_id"))
                token_ids = [
                    token_id
                    for token_id in (
                        _forward_clean_str(outcome.get("token_id")),
                        _forward_clean_str(outcome.get("no_token_id")),
                    )
                    if token_id is not None
                ]
                provenance = {
                    "writer": "log_market_source_contract_topology_facts",
                    "source": "gamma_market_scanner",
                    "recorded_at": recorded_at_value,
                    "market_slug": market_slug,
                    "event_id": event_id,
                    "event_slug": market_slug,
                    "condition_id": condition_id,
                    "question_id": question_id,
                    "outcome_title": _forward_clean_str(outcome.get("title")),
                    "city": city_name,
                    "target_date": target_date,
                    "temperature_metric": temperature_metric,
                    "resolution_sources": resolution_sources,
                    "source_contract": source_contract,
                }
                topology_id = "market_source_contract:{}:{}:{}:{}:{}".format(
                    market_slug,
                    condition_id,
                    city_name,
                    target_date,
                    temperature_metric,
                )
                write_market_topology_state(
                    _wconn,
                    topology_id=topology_id,
                    market_family="weather_temperature",
                    condition_id=condition_id,
                    status="CURRENT",
                    source_contract_status="MATCH",
                    authority_status="VERIFIED",
                    event_id=event_id,
                    question_id=question_id,
                    city_id=city_name,
                    city_timezone=city_timezone,
                    target_local_date=target_date,
                    temperature_metric=temperature_metric,
                    physical_quantity="temperature",
                    observation_field=observation_field,
                    data_version=data_version,
                    token_ids_json=token_ids,
                    source_contract_reason=_forward_clean_str(source_contract.get("reason")),
                    provenance_json=provenance,
                )
                _wconn.execute(
                    "UPDATE market_topology_state SET recorded_at = ? WHERE topology_id = ?",
                    (recorded_at_value, topology_id),
                )
                counts["topology_rows_written"] += 1

        _wconn.commit()
        status = "written" if counts["topology_rows_written"] else "skipped_no_valid_rows"
        return {
            "status": status,
            "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES,
            **counts,
        }
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning("Failed to log market source contract topology facts: %s", e)
        return {"status": "error", "tables": _MARKET_SOURCE_CONTRACT_TOPOLOGY_TABLES, "error": str(e)}
    finally:
        _wconn.close()


def append_source_contract_audit_events(
    conn: sqlite3.Connection | None,  # Deprecated: ignored when db_path is None; use db_path for explicit audit DB routing
    *,
    report: dict,
    db_path: "Path | None" = None,
) -> dict:
    """Append source-contract watch evidence without affecting eligibility.

    INV-37 fix (wave-2, 2026-05-18): opens its own connection rather than
    accepting an opaque conn from callers. Pre-fix, callers could pass a
    trades-rooted or forecasts-ATTACHed conn, causing writes to land in the wrong
    DB. The ``conn`` parameter is kept for backward compat but is no longer used.

    When ``db_path`` is provided (e.g. from ``--audit-db-path`` CLI flag), writes
    to that specific file instead of the canonical world DB. This is the only
    legitimate escape hatch: the caller has explicitly opened and initialised the
    target DB (via ``init_schema``) and is intentionally writing to a separate
    audit file, not substituting a wrong-DB connection.
    """
    if not isinstance(report, dict):
        return {"status": "refused_invalid_report", "tables": _SOURCE_CONTRACT_AUDIT_TABLES}

    if db_path is not None:
        from pathlib import Path as _Path
        _wconn = get_connection(_Path(db_path), write_class="bulk")
        # Initialise schema for a fresh audit DB (mirrors prior explicit init_schema call
        # in watch_source_contract.py::persist_audit_report before wave-2 refactor).
        init_schema(_wconn)
    else:
        _wconn = get_world_connection(write_class="live")
    try:
        missing_tables = [
            table for table in _SOURCE_CONTRACT_AUDIT_TABLES if not _table_exists(_wconn, table)
        ]
        if missing_tables:
            return {
                "status": "skipped_missing_tables",
                "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
                "missing_tables": tuple(missing_tables),
            }

        missing_columns = tuple(
            sorted(
                set(_SOURCE_CONTRACT_AUDIT_REQUIRED_COLUMNS)
                - _table_columns(_wconn, "source_contract_audit_events")
            )
        )
        if missing_columns:
            return {
                "status": "skipped_invalid_schema",
                "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
                "missing_columns": {"source_contract_audit_events": missing_columns},
            }

        checked_at_utc = _forward_clean_str(report.get("checked_at_utc"))
        scan_authority = _forward_clean_str(report.get("authority"))
        if checked_at_utc is None or scan_authority is None:
            return {"status": "refused_missing_scan_metadata", "tables": _SOURCE_CONTRACT_AUDIT_TABLES}
        if scan_authority not in _SOURCE_CONTRACT_AUDIT_AUTHORITIES:
            return {
                "status": "refused_invalid_scan_authority",
                "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
                "scan_authority": scan_authority,
            }
        events = report.get("events") or []
        if not isinstance(events, list):
            return {"status": "refused_invalid_report", "tables": _SOURCE_CONTRACT_AUDIT_TABLES}

        counts = {
            "audit_rows_inserted": 0,
            "audit_rows_unchanged": 0,
            "events_skipped_missing_facts": 0,
            "events_refused_invalid_facts": 0,
        }
        report_status = _forward_clean_str(report.get("status"))
        if report_status is not None and report_status not in _SOURCE_CONTRACT_AUDIT_REPORT_STATUSES:
            return {
                "status": "refused_invalid_report_status",
                "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
                "report_status": report_status,
            }

        for event in events:
            if not isinstance(event, dict):
                counts["events_skipped_missing_facts"] += 1
                continue
            source_contract = event.get("source_contract") or {}
            if not isinstance(source_contract, dict):
                counts["events_skipped_missing_facts"] += 1
                continue
            event_id = _forward_clean_str(event.get("event_id") or event.get("slug"))
            source_contract_status = _forward_clean_str(source_contract.get("status")) or "UNKNOWN"
            severity = _forward_clean_str(event.get("severity")) or "WARN"
            if event_id is None:
                counts["events_skipped_missing_facts"] += 1
                continue
            if severity not in _SOURCE_CONTRACT_AUDIT_SEVERITIES:
                counts["events_refused_invalid_facts"] += 1
                continue
            if source_contract_status not in _SOURCE_CONTRACT_AUDIT_STATUSES:
                counts["events_refused_invalid_facts"] += 1
                continue

            resolution_sources = list(source_contract.get("resolution_sources") or [])
            resolution_sources_json = json.dumps(
                resolution_sources,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            source_contract_json = json.dumps(
                source_contract,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            payload = {
                "checked_at_utc": checked_at_utc,
                "scan_authority": scan_authority,
                "report_status": report_status,
                "event_id": event_id,
                "slug": _forward_clean_str(event.get("slug")),
                "city": _forward_clean_str(event.get("city")),
                "target_date": _forward_clean_str(event.get("target_date")),
                "temperature_metric": _forward_metric(event.get("temperature_metric")),
                "severity": severity,
                "source_contract": source_contract,
            }
            payload_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
            audit_id = hashlib.sha256(
                f"{checked_at_utc}|{event_id}|{payload_hash}".encode("utf-8")
            ).hexdigest()
            cursor = _wconn.execute(
                """
                INSERT OR IGNORE INTO source_contract_audit_events (
                    audit_id, checked_at_utc, scan_authority, report_status, severity,
                    event_id, slug, title, city, target_date, temperature_metric,
                    source_contract_status, source_contract_reason,
                    configured_source_family, configured_station_id,
                    observed_source_family, observed_station_id,
                    resolution_sources_json, source_contract_json, payload_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    checked_at_utc,
                    scan_authority,
                    report_status,
                    severity,
                    event_id,
                    _forward_clean_str(event.get("slug")),
                    _forward_clean_str(event.get("title")),
                    _forward_clean_str(event.get("city")),
                    _forward_clean_str(event.get("target_date")),
                    _forward_metric(event.get("temperature_metric")),
                    source_contract_status,
                    _forward_clean_str(source_contract.get("reason")),
                    _forward_clean_str(source_contract.get("configured_source_family")),
                    _forward_clean_str(source_contract.get("configured_station_id")),
                    _forward_clean_str(source_contract.get("source_family")),
                    _forward_clean_str(source_contract.get("station_id")),
                    resolution_sources_json,
                    source_contract_json,
                    payload_hash,
                ),
            )
            if cursor.rowcount:
                counts["audit_rows_inserted"] += 1
            else:
                counts["audit_rows_unchanged"] += 1

        _wconn.commit()
        status = "written" if counts["audit_rows_inserted"] else "skipped_no_valid_rows"
        if counts["audit_rows_unchanged"] and not counts["audit_rows_inserted"]:
            status = "unchanged"
        return {
            "status": status,
            "tables": _SOURCE_CONTRACT_AUDIT_TABLES,
            **counts,
        }
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning("Failed to append source contract audit events: %s", e)
        return {"status": "error", "tables": _SOURCE_CONTRACT_AUDIT_TABLES, "error": str(e)}
    finally:
        _wconn.close()


@capability("settlement_write", lease=True)
def log_settlement(
    conn: sqlite3.Connection | None,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    market_slug: str | None,
    winning_bin: str | None,
    settlement_value: float | None,
    settlement_source: str | None,
    settled_at: str | None,
    authority: str,
    provenance: dict | None = None,
    recorded_at: str | None = None,
    settlement_unit: str | None = None,
) -> dict:
    """Mirror harvester settlement truth into settlement_outcomes.

    B3cont (2026-05-28): table renamed from settlement_outcomes.
    The helper is intentionally substrate-only: it never opens a default DB,
    never creates/migrates tables, never commits, and never infers missing
    market identity.
    """
    table = "settlement_outcomes"
    if conn is None:
        return {"status": "skipped_no_connection", "table": table}
    if not _table_exists(conn, table):
        return {"status": "skipped_missing_table", "table": table}

    required_columns = set(_SETTLEMENT_V2_COLUMNS)
    missing_columns = tuple(sorted(required_columns - _table_columns(conn, table)))
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_columns": missing_columns,
        }
    unique_key = ("city", "target_date", "temperature_metric")
    if not _table_has_unique_key(conn, table, unique_key):
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_unique_key": unique_key,
        }

    clean_city = _forward_clean_str(city)
    clean_target_date = _forward_clean_str(target_date)
    clean_metric = _forward_metric(temperature_metric)
    clean_market_slug = _forward_clean_str(market_slug)
    clean_authority = _forward_clean_str(authority)
    if not (clean_city and clean_target_date and clean_metric and clean_market_slug):
        return {
            "status": "refused_missing_identity",
            "table": table,
            "missing_fields": tuple(
                field
                for field, value in (
                    ("city", clean_city),
                    ("target_date", clean_target_date),
                    ("temperature_metric", clean_metric),
                    ("market_slug", clean_market_slug),
                )
                if not value
            ),
        }
    if clean_authority not in {"VERIFIED", "UNVERIFIED", "QUARANTINED"}:
        return {
            "status": "refused_invalid_authority",
            "table": table,
            "authority": authority,
        }

    recorded_at_value = _forward_clean_str(recorded_at) or datetime.now(timezone.utc).isoformat()
    provenance_payload = dict(provenance or {})
    provenance_payload.setdefault("legacy_table", "settlements")
    provenance_json = json.dumps(provenance_payload, sort_keys=True, default=str)

    try:
        conn.execute(
            """
            INSERT INTO settlement_outcomes (
                city, target_date, temperature_metric, market_slug, winning_bin,
                settlement_value, settlement_source, settled_at, authority,
                provenance_json, recorded_at, settlement_unit
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(city, target_date, temperature_metric) DO UPDATE SET
                market_slug=excluded.market_slug,
                winning_bin=excluded.winning_bin,
                settlement_value=excluded.settlement_value,
                settlement_source=excluded.settlement_source,
                settled_at=excluded.settled_at,
                authority=excluded.authority,
                provenance_json=excluded.provenance_json,
                recorded_at=excluded.recorded_at,
                settlement_unit=excluded.settlement_unit
            """,
            (
                clean_city,
                clean_target_date,
                clean_metric,
                clean_market_slug,
                winning_bin,
                settlement_value,
                _forward_clean_str(settlement_source),
                _forward_clean_str(settled_at),
                clean_authority,
                provenance_json,
                recorded_at_value,
                settlement_unit,
            ),
        )
    except sqlite3.OperationalError as exc:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "schema_error": str(exc),
        }
    return {"status": "written", "table": table}


def _market_event_outcome_public_result(result: dict) -> dict:
    """Strip internal SQL parameters before exposing helper results."""
    return {key: value for key, value in result.items() if key != "update_values"}


def _prepare_market_event_outcome_update(
    conn: sqlite3.Connection | None,
    *,
    market_slug: str | None,
    city: str,
    target_date: str,
    temperature_metric: str,
    condition_id: str | None,
    token_id: str | None,
    outcome: str,
) -> dict:
    table = "market_events"
    if conn is None:
        return {"status": "skipped_no_connection", "table": table}
    if not _table_exists(conn, table):
        return {"status": "skipped_missing_table", "table": table}

    required_columns = set(_FORWARD_MARKET_EVENT_COLUMNS)
    missing_columns = tuple(sorted(required_columns - _table_columns(conn, table)))
    if missing_columns:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_columns": missing_columns,
        }
    unique_key = ("market_slug", "condition_id")
    if not _table_has_unique_key(conn, table, unique_key):
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "missing_unique_key": unique_key,
        }

    clean_market_slug = _forward_clean_str(market_slug)
    clean_city = _forward_city_name(city)
    clean_target_date = _forward_clean_str(target_date)
    clean_metric = _forward_metric(temperature_metric)
    clean_condition_id = _forward_clean_str(condition_id)
    clean_token_id = _forward_clean_str(token_id)
    clean_outcome = _forward_clean_str(outcome)
    if clean_outcome is not None:
        clean_outcome = clean_outcome.upper()

    identity_fields = (
        ("market_slug", clean_market_slug),
        ("city", clean_city),
        ("target_date", clean_target_date),
        ("temperature_metric", clean_metric),
        ("condition_id", clean_condition_id),
        ("token_id", clean_token_id),
    )
    if not all(value for _, value in identity_fields):
        return {
            "status": "refused_missing_identity",
            "table": table,
            "missing_fields": tuple(field for field, value in identity_fields if not value),
        }
    if clean_outcome not in _MARKET_EVENT_OUTCOME_VALUES:
        return {
            "status": "refused_invalid_outcome",
            "table": table,
            "outcome": outcome,
        }

    try:
        row = conn.execute(
            """
            SELECT city, target_date, temperature_metric, token_id, outcome
            FROM market_events
            WHERE market_slug = ? AND condition_id = ?
            """,
            (clean_market_slug, clean_condition_id),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        return {
            "status": "skipped_invalid_schema",
            "table": table,
            "schema_error": str(exc),
        }

    if row is None:
        return {
            "status": "skipped_missing_market_event",
            "table": table,
            "market_slug": clean_market_slug,
            "condition_id": clean_condition_id,
        }

    existing = dict(zip(("city", "target_date", "temperature_metric", "token_id", "outcome"), tuple(row)))
    mismatches = tuple(
        field
        for field, expected in (
            ("city", clean_city),
            ("target_date", clean_target_date),
            ("temperature_metric", clean_metric),
            ("token_id", clean_token_id),
        )
        if str(existing.get(field)) != str(expected)
    )
    if mismatches:
        return {
            "status": "refused_identity_mismatch",
            "table": table,
            "mismatched_fields": mismatches,
        }

    existing_outcome = _forward_clean_str(existing.get("outcome"))
    if existing_outcome is not None:
        existing_outcome = existing_outcome.upper()
        if existing_outcome == clean_outcome:
            return {"status": "unchanged", "table": table}
        return {
            "status": "conflict_existing_outcome",
            "table": table,
            "existing_outcome": existing_outcome,
            "incoming_outcome": clean_outcome,
        }

    return {
        "status": "ready",
        "table": table,
        "update_values": (
            clean_outcome,
            clean_market_slug,
            clean_condition_id,
            clean_token_id,
            clean_city,
            clean_target_date,
            clean_metric,
        ),
    }


def log_market_event_outcome(
    conn: sqlite3.Connection | None,
    *,
    market_slug: str | None,
    city: str,
    target_date: str,
    temperature_metric: str,
    condition_id: str | None,
    token_id: str | None,
    outcome: str,
) -> dict:
    """Write a resolved child-market outcome onto existing market_events substrate.

    This helper updates only an exact scanner-produced row. It never creates
    tables, inserts missing market identities, opens a default DB, commits, or
    overwrites a conflicting resolved outcome.
    """
    prepared = _prepare_market_event_outcome_update(
        conn,
        market_slug=market_slug,
        city=city,
        target_date=target_date,
        temperature_metric=temperature_metric,
        condition_id=condition_id,
        token_id=token_id,
        outcome=outcome,
    )
    if prepared.get("status") != "ready":
        return _market_event_outcome_public_result(prepared)

    try:
        conn.execute(
            _MARKET_EVENT_OUTCOME_UPDATE_SQL,
            prepared["update_values"],
        )
    except sqlite3.OperationalError as exc:
        return {
            "status": "skipped_invalid_schema",
            "table": "market_events",
            "schema_error": str(exc),
        }
    return {"status": "written", "table": "market_events"}


def log_market_event_outcomes(
    conn: sqlite3.Connection | None,
    *,
    market_slug: str | None,
    city: str,
    target_date: str,
    temperature_metric: str,
    outcomes: Iterable[dict],
) -> dict:
    """Batch-update market_events outcomes using exact child identities."""
    table = "market_events"
    counts = {
        "written": 0,
        "unchanged": 0,
        "skipped_missing_market_event": 0,
        "refused_missing_identity": 0,
        "refused_identity_mismatch": 0,
        "conflict_existing_outcome": 0,
        "refused_invalid_outcome": 0,
        "skipped_invalid_schema": 0,
        "skipped_missing_table": 0,
        "skipped_no_connection": 0,
    }
    prepared_updates: list[dict] = []
    details: list[dict] = []
    for outcome_row in outcomes:
        if not isinstance(outcome_row, dict):
            result = {
                "status": "refused_missing_identity",
                "table": table,
                "missing_fields": ("outcome",),
            }
        else:
            result = _prepare_market_event_outcome_update(
                conn,
                market_slug=market_slug,
                city=city,
                target_date=target_date,
                temperature_metric=temperature_metric,
                condition_id=outcome_row.get("condition_id"),
                token_id=outcome_row.get("token_id"),
                outcome=outcome_row.get("outcome"),
            )
        status = str(result.get("status", "unknown"))
        if status == "ready":
            prepared_updates.append(result)
            details.append({"status": "pending_write", "table": table})
        else:
            if status in counts:
                counts[status] += 1
            details.append(_market_event_outcome_public_result(result))
        if status in {"skipped_no_connection", "skipped_missing_table", "skipped_invalid_schema"}:
            break

    blocking_statuses = {
        "skipped_missing_market_event",
        "refused_missing_identity",
        "refused_identity_mismatch",
        "conflict_existing_outcome",
        "refused_invalid_outcome",
        "skipped_invalid_schema",
        "skipped_missing_table",
        "skipped_no_connection",
    }
    if any(counts[key] for key in blocking_statuses):
        if counts["skipped_no_connection"]:
            status = "skipped_no_connection"
        elif counts["skipped_missing_table"]:
            status = "skipped_missing_table"
        elif counts["skipped_invalid_schema"]:
            status = "skipped_invalid_schema"
        elif counts["conflict_existing_outcome"] or counts["refused_identity_mismatch"]:
            status = "conflicted"
        else:
            status = "skipped_no_updates"
        return {"status": status, "table": table, **counts, "details": tuple(details)}

    if prepared_updates:
        try:
            conn.execute("SAVEPOINT market_events_outcome_batch")
            for result in prepared_updates:
                conn.execute(
                    _MARKET_EVENT_OUTCOME_UPDATE_SQL,
                    result["update_values"],
                )
            conn.execute("RELEASE SAVEPOINT market_events_outcome_batch")
        except sqlite3.OperationalError as exc:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT market_events_outcome_batch")
                conn.execute("RELEASE SAVEPOINT market_events_outcome_batch")
            except sqlite3.OperationalError:
                pass
            counts["skipped_invalid_schema"] += 1
            details.append(
                {
                    "status": "skipped_invalid_schema",
                    "table": table,
                    "schema_error": str(exc),
                }
            )
            return {
                "status": "skipped_invalid_schema",
                "table": table,
                **counts,
                "details": tuple(details),
            }
        counts["written"] = len(prepared_updates)
        details = [
            {"status": "written", "table": table}
            if detail.get("status") == "pending_write"
            else detail
            for detail in details
        ]
        status = "written"
    elif counts["unchanged"]:
        status = "unchanged"
    else:
        status = "skipped_no_updates"

    return {"status": status, "table": table, **counts, "details": tuple(details)}


def log_microstructure(conn, token_id: str, city: str, target_date: str, range_label: str,
                       price: float, volume: float, bid: float, ask: float, spread: float, source_timestamp: str):
    """Log microstructure snapshot (Spec injection point 7)."""
    try:
        conn.execute("""
            INSERT INTO token_price_log
            (token_id, city, target_date, range_label, price, volume, bid, ask, spread, source_timestamp, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))
        """, (token_id, city, target_date, range_label, price, volume, bid, ask, spread, source_timestamp))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning('Failed to log microstructure: %s', e)


def log_rescue_event(
    conn,
    *,
    trade_id: str,
    chain_state: str,
    reason: str,
    occurred_at: str,
    temperature_metric: str,
    causality_status: str = "OK",
    authority: str = "UNVERIFIED",
    authority_source=None,
    position_id=None,
    decision_snapshot_id=None,
) -> None:
    """B063: append a durable audit row for a chain-rescue event.

    Writes to `rescue_events` (Phase 2 schema). Unlike the existing
    CHAIN_RESCUE_AUDIT row in position_events, this row carries the
    temperature_metric, causality_status, and provenance authority
    needed to distinguish a legitimate low-lane N/A_CAUSAL skip from
    a silent rescue loss.

    Per SD-1 (MetricIdentity is binary) and SD-H (provenance authority
    tagging), temperature_metric stays in {'high','low'} and the
    `authority` column carries the tri-state confidence. Callers must
    resolve ambiguity via `authority='UNVERIFIED'` + concrete high/low
    tag rather than introducing a third temperature_metric value.

    Exempt from the DT#1 commit_then_export choke point — the audit row
    IS the authoritative observability record, not a derived export,
    and must be durable before the cycle acknowledges the rescue
    outcome. Same rule the existing CHAIN_RESCUE_AUDIT row follows.

    Fails closed-soft: if the table is missing on legacy DBs or the
    write raises, the error is logged but NOT re-raised, because the
    caller (chain_reconciliation._emit_rescue_event) must continue
    reconciling chain state even when the audit row cannot be
    persisted. The pre-existing CHAIN_RESCUE_AUDIT row in position_events
    provides a legacy-path audit trail as fallback.
    """
    import logging
    _logger = logging.getLogger(__name__)
    if conn is None:
        _logger.warning(
            "log_rescue_event: conn is None, skipping rescue_events write for trade_id=%s",
            trade_id,
        )
        return
    if temperature_metric not in ("high", "low"):
        _logger.error(
            "log_rescue_event: invalid temperature_metric=%r for trade_id=%s; skipping rescue_events write",
            temperature_metric,
            trade_id,
        )
        return
    try:
        conn.execute(
            """
            INSERT INTO rescue_events
                (trade_id, position_id, decision_snapshot_id,
                 temperature_metric, causality_status,
                 authority, authority_source,
                 chain_state, reason, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                position_id,
                decision_snapshot_id,
                temperature_metric,
                causality_status,
                authority,
                authority_source,
                chain_state,
                reason,
                occurred_at,
            ),
        )
    except sqlite3.OperationalError as exc:
        _logger.warning(
            "log_rescue_event: rescue_events write failed for trade_id=%s: %s",
            trade_id,
            exc,
        )
    except sqlite3.IntegrityError as exc:
        _logger.info(
            "log_rescue_event: idempotent duplicate for trade_id=%s occurred_at=%s: %s",
            trade_id,
            occurred_at,
            exc,
        )


def _bin_type_for_label(label: str) -> str:
    lower = (label or "").lower()
    if "or below" in lower:
        return "shoulder_low"
    if "or higher" in lower or "or above" in lower:
        return "shoulder_high"
    return "center"


def _coerce_snapshot_fk(value) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _local_legacy_snapshot_fk(conn: sqlite3.Connection, value) -> Optional[int]:
    """Resolve a snapshot id reference for trade_decisions.forecast_snapshot_id.

    v1.F20 (2026-05-18): the legacy ensemble_snapshots table was dropped. The
    canonical table is ensemble_snapshots in zeus-forecasts.db, which is
    ATTACHed as 'forecasts' by get_trade_connection_with_world().

    Validates existence in forecasts.ensemble_snapshots when available;
    falls back to trusting the coerced id when the ATTACH is absent (e.g.
    non-trade connections in tests). Returns None only when value is absent
    or the snapshot row genuinely does not exist.
    """
    snapshot_id = _coerce_snapshot_fk(value)
    if snapshot_id is None:
        return None
    try:
        fk_rows = conn.execute("PRAGMA foreign_key_list(trade_decisions)").fetchall()
        has_legacy_snapshot_fk = any(
            (row["table"] if hasattr(row, "keys") else row[2]) == "ensemble_snapshots"
            for row in fk_rows
        )
    except sqlite3.OperationalError:
        has_legacy_snapshot_fk = False
    if has_legacy_snapshot_fk:
        if not _table_exists(conn, "ensemble_snapshots"):
            return None
        try:
            row = conn.execute(
                "SELECT 1 FROM ensemble_snapshots WHERE snapshot_id = ? LIMIT 1",
                (snapshot_id,),
            ).fetchone()
            return snapshot_id if row is not None else None
        except sqlite3.OperationalError:
            return None
    # Primary path: validate against forecasts.ensemble_snapshots (K1 canonical).
    # Use try/except rather than _table_exists() because that helper only queries
    # sqlite_master in the *main* schema, not in ATTACHed schemas (forecasts.*).
    try:
        row = conn.execute(
            "SELECT 1 FROM forecasts.ensemble_snapshots WHERE snapshot_id = ? LIMIT 1",
            (snapshot_id,),
        ).fetchone()
        return snapshot_id if row is not None else None
    except sqlite3.OperationalError:
        # forecasts schema not attached. Older physical trade_decisions tables
        # may still carry a local FK to legacy ensemble_snapshots even though K1
        # moved the canonical table to zeus-forecasts.db. Only return the id if
        # the local legacy table exists and contains it; otherwise NULL avoids a
        # dead foreign-key edge from dropping exit-audit evidence.
        if not _table_exists(conn, "ensemble_snapshots"):
            return None
        try:
            row = conn.execute(
                "SELECT 1 FROM ensemble_snapshots WHERE snapshot_id = ? LIMIT 1",
                (snapshot_id,),
            ).fetchone()
            return snapshot_id if row is not None else None
        except sqlite3.OperationalError:
            return None


def _normalize_opportunity_availability_status(value: str) -> str:
    status = str(value or "").strip().upper()
    if not status:
        return "ok"
    mapping = {
        "OK": "ok",
        "MISSING": "missing",
        "DATA_MISSING": "missing",
        "DATA_STALE": "stale",
        "STALE": "stale",
        "RATE_LIMITED": "rate_limited",
        "UNAVAILABLE": "unavailable",
        "DATA_UNAVAILABLE": "unavailable",
        "CHAIN_UNAVAILABLE": "chain_unavailable",
    }
    return mapping.get(status, "unavailable")


def _candidate_city_name(candidate) -> str:
    city = getattr(candidate, "city", "")
    return str(getattr(city, "name", city) or "")


def _opportunity_fact_candidate_id(candidate) -> str:
    event_id = str(getattr(candidate, "event_id", "") or "").strip()
    if event_id:
        return event_id
    slug = str(getattr(candidate, "slug", "") or "").strip()
    if slug:
        return slug
    city_name = _candidate_city_name(candidate)
    target_date = str(getattr(candidate, "target_date", "") or "").strip()
    if city_name and target_date:
        return f"{city_name}:{target_date}"
    return ""


def _main_database_path(conn: sqlite3.Connection | None) -> Path | None:
    if conn is None:
        return None
    try:
        for row in conn.execute("PRAGMA database_list").fetchall():
            name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
            path = row["file"] if isinstance(row, sqlite3.Row) else row[2]
            if name == "main" and path:
                return Path(str(path)).resolve()
    except Exception:
        return None
    return None


def _is_verified_trade_connection(conn: sqlite3.Connection | None) -> bool:
    main_path = _main_database_path(conn)
    if main_path is None:
        return False
    try:
        return main_path == _zeus_trade_db_path().resolve()
    except Exception:
        return False


def _decision_vector_value(decision, attr_name: str) -> float | None:
    edge = getattr(decision, "edge", None)
    vector = getattr(decision, attr_name, None)
    if edge is None or vector is None:
        return None
    direction = str(getattr(edge, "direction", "") or "")
    try:
        values = vector.tolist() if hasattr(vector, "tolist") else list(vector)
    except TypeError:
        return None
    label = str(getattr(getattr(edge, "bin", None), "label", "") or "")
    bin_labels = []
    try:
        bin_labels = list(getattr(decision, "bin_labels", []) or [])
    except TypeError:
        bin_labels = []
    if not label or not bin_labels:
        return None
    try:
        idx = bin_labels.index(label)
    except ValueError:
        return None
    if idx >= len(values):
        return None
    try:
        probability = float(values[idx])
    except (TypeError, ValueError):
        return None
    if probability != probability or probability in (float("inf"), float("-inf")):
        return None
    if direction == "buy_no":
        probability = 1.0 - probability
    return probability


def _json_probability_vector(value) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    try:
        values = value.tolist() if hasattr(value, "tolist") else list(value)
    except TypeError:
        return None, False
    return json.dumps(values, ensure_ascii=False), len(values) > 0


def _candidate_bin_labels(candidate) -> list[str]:
    labels: list[str] = []
    try:
        outcomes = list(getattr(candidate, "outcomes", []) or [])
    except TypeError:
        return labels
    for outcome in outcomes:
        if outcome.get("range_low") is None and outcome.get("range_high") is None:
            continue
        title = str(outcome.get("title", "") or "")
        if title:
            labels.append(title)
    return labels


def _trace_direction(decision) -> str:
    edge = getattr(decision, "edge", None)
    direction = str(getattr(edge, "direction", "") or "unknown")
    return direction if direction in {"buy_yes", "buy_no", "unknown"} else "unknown"


def _trace_range_label(decision) -> str:
    edge = getattr(decision, "edge", None)
    return str(getattr(getattr(edge, "bin", None), "label", "") or "")


def _trace_scalar_posterior(decision) -> float | None:
    edge = getattr(decision, "edge", None)
    if edge is not None:
        try:
            return float(getattr(edge, "p_posterior", None))
        except (TypeError, ValueError):
            return None
    edge_context = getattr(decision, "edge_context", None)
    if edge_context is not None:
        try:
            return float(getattr(edge_context, "p_posterior", None))
        except (TypeError, ValueError):
            return None
    return None


def _trace_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _trace_float(value) -> float | None:
    """Coerce value to float for probability_trace_fact REAL columns; None on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def log_probability_trace_fact(
    conn: sqlite3.Connection | None,  # Deprecated: ignored; function opens its own world connection (INV-37 fix, PR-S4b §3)
    *,
    candidate,
    decision,
    recorded_at: str,
    mode: str,
) -> dict:
    """Write one durable probability trace row for one decision to zeus-world.db.

    This helper intentionally stores direct decision-time vectors only. It must
    not scalar-backfill vector lineage from BinEdge scalar fields.

    INV-37 fix (PR-S4b §3, 2026-05-18): opens its own world connection rather
    than accepting an opaque conn from callers. Pre-fix, callers passed the
    cycle trades-rooted conn (zeus_trades.db MAIN with world ATTACHed), causing
    probability_trace_fact rows to land in zeus_trades.db instead of zeus-world.db.
    The ``conn`` parameter is accepted for backward compatibility but ignored.
    Previously a ``None`` conn short-circuited with ``{"status": "skipped_no_connection"}``;
    now a write is performed unconditionally against zeus-world.db regardless of
    the value passed. Callers that relied on the skip behaviour must be updated.
    """
    conn = get_world_connection()
    try:
        return _log_probability_trace_fact_inner(conn, candidate=candidate, decision=decision, recorded_at=recorded_at, mode=mode)
    finally:
        conn.close()


def _log_probability_trace_fact_inner(
    conn: "sqlite3.Connection",
    *,
    candidate,
    decision,
    recorded_at: str,
    mode: str,
) -> dict:
    if not _table_exists(conn, "probability_trace_fact"):
        logger.info("Probability trace table unavailable; skipping durable write")
        return {"status": "skipped_missing_table", "table": "probability_trace_fact"}

    decision_id = str(getattr(decision, "decision_id", "") or "").strip()
    if not decision_id:
        return {"status": "skipped_missing_decision_id", "table": "probability_trace_fact"}

    p_raw_json, has_p_raw = _json_probability_vector(getattr(decision, "p_raw", None))
    p_cal_json, has_p_cal = _json_probability_vector(getattr(decision, "p_cal", None))
    p_market_json, has_p_market = _json_probability_vector(getattr(decision, "p_market", None))
    p_posterior_json, _has_p_posterior_vector = _json_probability_vector(
        getattr(decision, "p_posterior_vector", None)
    )

    missing: list[str] = []
    for name, present in (
        ("p_raw_json", has_p_raw),
        ("p_cal_json", has_p_cal),
        ("p_market_json", has_p_market),
    ):
        if not present:
            missing.append(name)

    if not has_p_raw and not has_p_cal and not has_p_market:
        trace_status = "pre_vector_unavailable"
    elif not (has_p_raw and has_p_cal and has_p_market):
        trace_status = "degraded_missing_vectors"
    elif str(getattr(decision, "availability_status", "") or "").strip().upper() not in {"", "OK"}:
        trace_status = "degraded_decision_context"
    else:
        trace_status = "complete"

    rejection_stage = str(getattr(decision, "rejection_stage", "") or "")
    availability_status = str(getattr(decision, "availability_status", "") or "")
    missing_reasons = {
        "missing_vectors": missing,
        "rejection_stage": rejection_stage,
        "availability_status": availability_status,
    }
    bin_labels = _candidate_bin_labels(candidate)
    alpha = getattr(decision, "alpha", None)
    try:
        alpha = float(alpha) if alpha not in (None, "") else None
    except (TypeError, ValueError):
        alpha = None

    # P2 (PLAN_v3 §6.P2 stage 3): MarketPhase axis A — decision tag.
    # EdgeDecision.market_phase is the str-form ``.value`` stamped at the
    # cycle_runtime call site after evaluate_candidate returns; falls back
    # to the candidate's tag if the decision was constructed before
    # stage-2 plumbing (legacy / test fixtures). None when neither side
    # carries a tag (off-cycle / manual writes).
    market_phase_value: str | None = None
    decision_phase = getattr(decision, "market_phase", None)
    if decision_phase is not None:
        market_phase_value = decision_phase.value if hasattr(decision_phase, "value") else str(decision_phase)
    else:
        candidate_phase = getattr(candidate, "market_phase", None)
        if candidate_phase is not None:
            market_phase_value = candidate_phase.value if hasattr(candidate_phase, "value") else str(candidate_phase)

    # Idempotent ALTER TABLE migration for LIVE-PROB-P0 columns (SV=34 tail-mass
    # + SV=35 edge-bin sanity telemetry).  Mirrors the opportunity_fact pattern in
    # log_opportunity_fact: check existing columns once, issue ALTER only if absent.
    # Necessary so the INSERT below succeeds on existing DBs that predate SV=34/35
    # (the same gap init_schema's ALTER loop covers for boot-path callers, but this
    # writer can be called on a DB opened without init_schema — e.g. test fixtures
    # or daemon paths that open a pre-existing world DB directly).
    _ptf_cols = _table_columns(conn, "probability_trace_fact")
    for _col, _type in (
        ("prob_tail_mass_cal", "REAL"),
        ("prob_tail_mass_market", "REAL"),
        ("prob_tail_entropy", "REAL"),
        ("probability_sanity_mode", "TEXT"),
        ("probability_sanity_reason", "TEXT"),
        ("edge_bin_idx", "INTEGER"),
        ("edge_bin_label", "TEXT"),
        ("edge_bin_p_raw", "REAL"),
        ("edge_bin_p_cal", "REAL"),
        ("edge_bin_p_market", "REAL"),
        ("edge_bin_member_support", "REAL"),
        ("edge_bin_odds_ratio", "REAL"),
        ("near_tail_p_cal", "REAL"),
        ("near_tail_p_market", "REAL"),
    ):
        if _col not in _ptf_cols:
            try:
                conn.execute(
                    f"ALTER TABLE probability_trace_fact ADD COLUMN {_col} {_type};"
                )
            except sqlite3.OperationalError:
                pass  # concurrent add / already present

    conn.execute(
        """
        INSERT INTO probability_trace_fact (
            trace_id,
            decision_id,
            decision_snapshot_id,
            candidate_id,
            city,
            target_date,
            range_label,
            direction,
            mode,
            strategy_key,
            discovery_mode,
            entry_method,
            selected_method,
            trace_status,
            missing_reason_json,
            bin_labels_json,
            p_raw_json,
            p_cal_json,
            p_market_json,
            p_posterior_json,
            p_posterior,
            alpha,
            agreement,
            n_edges_found,
            n_edges_after_fdr,
            rejection_stage,
            availability_status,
            market_phase,
            prob_tail_mass_cal,
            prob_tail_mass_market,
            prob_tail_entropy,
            probability_sanity_mode,
            probability_sanity_reason,
            edge_bin_idx,
            edge_bin_label,
            edge_bin_p_raw,
            edge_bin_p_cal,
            edge_bin_p_market,
            edge_bin_member_support,
            edge_bin_odds_ratio,
            near_tail_p_cal,
            near_tail_p_market,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trace_id) DO UPDATE SET
            decision_id=excluded.decision_id,
            decision_snapshot_id=excluded.decision_snapshot_id,
            candidate_id=excluded.candidate_id,
            city=excluded.city,
            target_date=excluded.target_date,
            range_label=excluded.range_label,
            direction=excluded.direction,
            mode=excluded.mode,
            strategy_key=excluded.strategy_key,
            discovery_mode=excluded.discovery_mode,
            entry_method=excluded.entry_method,
            selected_method=excluded.selected_method,
            trace_status=excluded.trace_status,
            missing_reason_json=excluded.missing_reason_json,
            bin_labels_json=excluded.bin_labels_json,
            p_raw_json=excluded.p_raw_json,
            p_cal_json=excluded.p_cal_json,
            p_market_json=excluded.p_market_json,
            p_posterior_json=excluded.p_posterior_json,
            p_posterior=excluded.p_posterior,
            alpha=excluded.alpha,
            agreement=excluded.agreement,
            n_edges_found=excluded.n_edges_found,
            n_edges_after_fdr=excluded.n_edges_after_fdr,
            rejection_stage=excluded.rejection_stage,
            availability_status=excluded.availability_status,
            market_phase=excluded.market_phase,
            prob_tail_mass_cal=excluded.prob_tail_mass_cal,
            prob_tail_mass_market=excluded.prob_tail_mass_market,
            prob_tail_entropy=excluded.prob_tail_entropy,
            probability_sanity_mode=excluded.probability_sanity_mode,
            probability_sanity_reason=excluded.probability_sanity_reason,
            edge_bin_idx=excluded.edge_bin_idx,
            edge_bin_label=excluded.edge_bin_label,
            edge_bin_p_raw=excluded.edge_bin_p_raw,
            edge_bin_p_cal=excluded.edge_bin_p_cal,
            edge_bin_p_market=excluded.edge_bin_p_market,
            edge_bin_member_support=excluded.edge_bin_member_support,
            edge_bin_odds_ratio=excluded.edge_bin_odds_ratio,
            near_tail_p_cal=excluded.near_tail_p_cal,
            near_tail_p_market=excluded.near_tail_p_market,
            recorded_at=excluded.recorded_at
        """,
        (
            f"probtrace:{decision_id}",
            decision_id,
            str(getattr(decision, "decision_snapshot_id", "") or "") or None,
            _opportunity_fact_candidate_id(candidate) or None,
            _candidate_city_name(candidate) or None,
            str(getattr(candidate, "target_date", "") or "") or None,
            _trace_range_label(decision) or None,
            _trace_direction(decision),
            str(mode or "") or None,
            str(getattr(decision, "strategy_key", "") or "").strip() or None,
            str(getattr(candidate, "discovery_mode", "") or "") or None,
            str(getattr(decision, "selected_method", "") or getattr(decision, "entry_method", "") or "") or None,
            str(getattr(decision, "selected_method", "") or "") or None,
            trace_status,
            json.dumps(missing_reasons, ensure_ascii=False, sort_keys=True),
            json.dumps(bin_labels, ensure_ascii=False),
            p_raw_json,
            p_cal_json,
            p_market_json,
            p_posterior_json,
            _trace_scalar_posterior(decision),
            alpha,
            str(getattr(decision, "agreement", "") or "") or None,
            _trace_int(getattr(decision, "n_edges_found", None)),
            _trace_int(getattr(decision, "n_edges_after_fdr", None)),
            rejection_stage or None,
            availability_status or None,
            market_phase_value,
            # LIVE-PROB-P0: tail-mass evidence columns. Read from decision attrs
            # stamped by the gate at evaluator.py:4622; None for legacy decisions
            # or decisions where p_market was unavailable.
            _trace_float(getattr(decision, "prob_tail_mass_cal", None)),
            _trace_float(getattr(decision, "prob_tail_mass_market", None)),
            _trace_float(getattr(decision, "prob_tail_entropy", None)),
            # LIVE-PROB-P0 §E (SCHEMA_VERSION 35, 2026-05-23): 11 edge-bin sanity telemetry columns.
            # Populated when probability_edge_bin_sanity() is called (non-day0 edges).
            # None for legacy rows, day0 decisions, or strategies not in apply_to_strategies.
            str(getattr(decision, "probability_sanity_mode", None) or "") or None,
            str(getattr(decision, "probability_sanity_reason", None) or "") or None,
            _trace_int(getattr(decision, "edge_bin_idx", None)),
            str(getattr(decision, "edge_bin_label", None) or "") or None,
            _trace_float(getattr(decision, "edge_bin_p_raw", None)),
            _trace_float(getattr(decision, "edge_bin_p_cal", None)),
            _trace_float(getattr(decision, "edge_bin_p_market", None)),
            _trace_float(getattr(decision, "edge_bin_member_support", None)),
            _trace_float(getattr(decision, "edge_bin_odds_ratio", None)),
            _trace_float(getattr(decision, "near_tail_p_cal", None)),
            _trace_float(getattr(decision, "near_tail_p_market", None)),
            recorded_at,
        ),
    )
    conn.commit()
    return {
        "status": "written",
        "table": "probability_trace_fact",
        "trace_status": trace_status,
    }


def query_probability_trace_completeness(conn: sqlite3.Connection | None) -> dict:
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "trace_rows": 0,
            "with_p_raw_json": 0,
            "with_p_cal_json": 0,
            "with_p_market_json": 0,
            "complete_rows": 0,
            "degraded_rows": 0,
            "pre_vector_rows": 0,
        }
    if not _table_exists(conn, "probability_trace_fact"):
        return {
            "status": "missing_table",
            "trace_rows": 0,
            "with_p_raw_json": 0,
            "with_p_cal_json": 0,
            "with_p_market_json": 0,
            "complete_rows": 0,
            "degraded_rows": 0,
            "pre_vector_rows": 0,
        }
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS trace_rows,
            SUM(CASE WHEN p_raw_json IS NOT NULL AND trim(p_raw_json) NOT IN ('', '[]') THEN 1 ELSE 0 END) AS with_p_raw_json,
            SUM(CASE WHEN p_cal_json IS NOT NULL AND trim(p_cal_json) NOT IN ('', '[]') THEN 1 ELSE 0 END) AS with_p_cal_json,
            SUM(CASE WHEN p_market_json IS NOT NULL AND trim(p_market_json) NOT IN ('', '[]') THEN 1 ELSE 0 END) AS with_p_market_json,
            SUM(CASE WHEN trace_status = 'complete' THEN 1 ELSE 0 END) AS complete_rows,
            SUM(CASE WHEN trace_status IN ('degraded_missing_vectors', 'degraded_decision_context') THEN 1 ELSE 0 END) AS degraded_rows,
            SUM(CASE WHEN trace_status = 'pre_vector_unavailable' THEN 1 ELSE 0 END) AS pre_vector_rows
        FROM probability_trace_fact
        """
    ).fetchone()
    return {
        "status": "ok",
        "trace_rows": int(row["trace_rows"] or 0),
        "with_p_raw_json": int(row["with_p_raw_json"] or 0),
        "with_p_cal_json": int(row["with_p_cal_json"] or 0),
        "with_p_market_json": int(row["with_p_market_json"] or 0),
        "complete_rows": int(row["complete_rows"] or 0),
        "degraded_rows": int(row["degraded_rows"] or 0),
        "pre_vector_rows": int(row["pre_vector_rows"] or 0),
    }



def _attached_table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    if schema not in {"world", "forecasts"}:
        return False
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _selection_fact_table_ref(
    conn: sqlite3.Connection,
    table: str,
    *,
    require_attached_world: bool = False,
) -> str | None:
    # B-series restore: selection_family_fact / selection_hypothesis_fact are
    # world_class (db_table_ownership.yaml) — writes MUST route to world.<table>
    # when world.db is ATTACHed, else they land in the wrong DB (INV-37/K1).
    if table not in {"selection_family_fact", "selection_hypothesis_fact"}:
        return None
    try:
        attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
        if "world" in attached and _attached_table_exists(conn, "world", table):
            return f"world.{table}"
    except sqlite3.Error:
        pass
    if require_attached_world:
        return None
    # Owner-routed (2026-06-30): selection_*_fact is world-owned. A bare `INSERT INTO <table>` lands in the
    # connection's MAIN, so returning the bare name is only correct when MAIN IS the owner (world). A conn
    # rooted at a wrong canonical DB (e.g. the evaluator's trade conn — the 412/3029 stray-ghost inversion)
    # would silently write a ghost; skip the write instead. Memory/tempfile/ad-hoc test conns keep the legacy
    # bare behavior so the suite is unaffected.
    from src.state.owner_routed_write import _KNOWN_DB_FILENAMES, owner_db_filename
    _main = _main_database_path(conn)
    _mainname = _main.name if _main is not None else None
    if _mainname == owner_db_filename(table):
        return table if _table_exists(conn, table) else None
    if _mainname is not None and _mainname in _KNOWN_DB_FILENAMES:
        return None  # known-but-wrong canonical DB -> a bare write would be a ghost
    if _table_exists(conn, table):
        return table
    return None


def log_selection_family_fact(
    conn: sqlite3.Connection | None,
    *,
    family_id: str,
    cycle_mode: str,
    created_at: str,
    meta: dict,
    decision_snapshot_id: str | None = None,
    city: str | None = None,
    target_date: str | None = None,
    strategy_key: str | None = None,
    discovery_mode: str | None = None,
    decision_time_status: str | None = None,
    require_attached_world: bool = False,
) -> dict:
    if conn is None:
        return {"status": "skipped_no_connection", "table": "selection_family_fact"}
    table_ref = _selection_fact_table_ref(
        conn,
        "selection_family_fact",
        require_attached_world=require_attached_world,
    )
    if table_ref is None:
        if require_attached_world:
            return {
                "status": "skipped_missing_canonical_world_table",
                "table": "selection_family_fact",
            }
        return {"status": "skipped_missing_table", "table": "selection_family_fact"}
    if not family_id:
        return {"status": "skipped_missing_family_id", "table": "selection_family_fact"}
    conn.execute(
        f"""
        INSERT INTO {table_ref} (
            family_id, cycle_mode, decision_snapshot_id, city, target_date,
            strategy_key, discovery_mode, created_at, meta_json, decision_time_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(family_id) DO UPDATE SET
            cycle_mode=excluded.cycle_mode,
            decision_snapshot_id=excluded.decision_snapshot_id,
            city=excluded.city,
            target_date=excluded.target_date,
            strategy_key=excluded.strategy_key,
            discovery_mode=excluded.discovery_mode,
            created_at=excluded.created_at,
            meta_json=excluded.meta_json,
            decision_time_status=excluded.decision_time_status
        """,
        (
            family_id,
            cycle_mode,
            decision_snapshot_id,
            city,
            target_date,
            strategy_key,
            discovery_mode,
            created_at,
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
            decision_time_status,
        ),
    )
    return {"status": "written", "table": "selection_family_fact"}


def log_selection_hypothesis_fact(
    conn: sqlite3.Connection | None,
    *,
    hypothesis_id: str,
    family_id: str,
    city: str,
    target_date: str,
    range_label: str,
    direction: str,
    recorded_at: str,
    meta: dict,
    decision_id: str | None = None,
    candidate_id: str | None = None,
    p_value: float | None = None,
    q_value: float | None = None,
    ci_lower: float | None = None,
    ci_upper: float | None = None,
    edge: float | None = None,
    tested: bool = True,
    passed_prefilter: bool = False,
    selected_post_fdr: bool = False,
    rejection_stage: str | None = None,
    require_attached_world: bool = False,
) -> dict:
    if conn is None:
        return {"status": "skipped_no_connection", "table": "selection_hypothesis_fact"}
    table_ref = _selection_fact_table_ref(
        conn,
        "selection_hypothesis_fact",
        require_attached_world=require_attached_world,
    )
    if table_ref is None:
        if require_attached_world:
            return {
                "status": "skipped_missing_canonical_world_table",
                "table": "selection_hypothesis_fact",
            }
        return {"status": "skipped_missing_table", "table": "selection_hypothesis_fact"}
    if not hypothesis_id:
        return {"status": "skipped_missing_hypothesis_id", "table": "selection_hypothesis_fact"}
    if not family_id:
        return {"status": "skipped_missing_family_id", "table": "selection_hypothesis_fact"}
    direction_value = direction if direction in {"buy_yes", "buy_no"} else "unknown"
    conn.execute(
        f"""
        INSERT INTO {table_ref} (
            hypothesis_id, family_id, decision_id, candidate_id, city, target_date,
            range_label, direction, p_value, q_value, ci_lower, ci_upper, edge,
            tested, passed_prefilter, selected_post_fdr, rejection_stage,
            recorded_at, meta_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(hypothesis_id) DO UPDATE SET
            family_id=excluded.family_id,
            decision_id=excluded.decision_id,
            candidate_id=excluded.candidate_id,
            city=excluded.city,
            target_date=excluded.target_date,
            range_label=excluded.range_label,
            direction=excluded.direction,
            p_value=excluded.p_value,
            q_value=excluded.q_value,
            ci_lower=excluded.ci_lower,
            ci_upper=excluded.ci_upper,
            edge=excluded.edge,
            tested=excluded.tested,
            passed_prefilter=excluded.passed_prefilter,
            selected_post_fdr=excluded.selected_post_fdr,
            rejection_stage=excluded.rejection_stage,
            recorded_at=excluded.recorded_at,
            meta_json=excluded.meta_json
        """,
        (
            hypothesis_id,
            family_id,
            decision_id,
            candidate_id,
            city,
            target_date,
            range_label,
            direction_value,
            p_value,
            q_value,
            ci_lower,
            ci_upper,
            edge,
            int(bool(tested)),
            int(bool(passed_prefilter)),
            int(bool(selected_post_fdr)),
            rejection_stage,
            recorded_at,
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
        ),
    )
    return {"status": "written", "table": "selection_hypothesis_fact"}




DATA_IMPROVEMENT_TABLES = (
    "probability_trace_fact",
    "calibration_decision_group",
    "selection_family_fact",
    "selection_hypothesis_fact",
)


def query_data_improvement_inventory(conn: sqlite3.Connection | None) -> dict:
    """Return DB-truth readiness/counts for data-improvement substrates."""
    if conn is None:
        return {"status": "skipped_no_connection", "tables": {}}
    inventory: dict[str, dict] = {}
    for table in DATA_IMPROVEMENT_TABLES:
        if not _table_exists(conn, table):
            inventory[table] = {"exists": False, "rows": 0}
            continue
        count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        inventory[table] = {"exists": True, "rows": count}
    missing = sorted(table for table, payload in inventory.items() if not payload["exists"])
    return {
        "status": "missing_tables" if missing else "ok",
        "tables": inventory,
        "missing_tables": missing,
    }


def log_settlement_day_observation_authority(
    conn: sqlite3.Connection | None,  # Deprecated: ignored; opens its own trade connection (INV-37)
    *,
    authority_id: str,
    city: str | None,
    target_date: str | None,
    temperature_metric: str | None,
    decision_time_utc: str | None,
    market_phase: str | None,
    source: str | None,
    station_id: str | None,
    observation_time_utc: str | None,
    first_sample_time_utc: str | None,
    last_sample_time_utc: str | None,
    high_so_far: float | None,
    low_so_far: float | None,
    current_temp: float | None,
    sample_count: int | None,
    coverage_status: str | None,
    freshness_status: str | None,
    local_date_matches_target: int | None,
    source_authorized_for_settlement: int | None,
    persisted_surface_available: int | None,
    payload_json: str | None,
    recorded_at: str,
) -> dict:
    """Write one settlement-day observation authority row to zeus_trades.db.

    OBS-AUTHORITY-FOUNDATION (2026-05-23). Captures the RUNTIME observation
    object (today invisible in the DB) for every settlement-day/day0 candidate
    — including the MISSING / STALE / LOW-coverage failure cases. Colocated on
    the trade DB with opportunity_fact (its runtime write target post-INV-37)
    so the operator audit query joins same-DB.

    OBSERVABILITY ONLY: this is a best-effort durable write. It never raises
    into the cycle, never gates a trade, and never changes selection. Same
    fail-open contract as log_opportunity_fact (warn + return on any error).
    """
    _wconn = conn if _is_verified_trade_connection(conn) else get_trade_connection(write_class="live")
    _owns_connection = _wconn is not conn
    try:
        if not _table_exists(_wconn, "settlement_day_observation_authority"):
            logger.info(
                "settlement_day_observation_authority table unavailable; skipping durable write"
            )
            return {"status": "skipped_missing_table", "table": "settlement_day_observation_authority"}

        _metric = str(temperature_metric or "").strip().lower() or None
        if _metric not in (None, "high", "low"):
            _metric = None

        def _as_int_bool(value) -> int | None:
            if value is None:
                return None
            return 1 if int(value) else 0

        _wconn.execute(
            """
            INSERT INTO settlement_day_observation_authority (
                authority_id,
                city,
                target_date,
                temperature_metric,
                decision_time_utc,
                market_phase,
                source,
                station_id,
                observation_time_utc,
                first_sample_time_utc,
                last_sample_time_utc,
                high_so_far,
                low_so_far,
                current_temp,
                sample_count,
                coverage_status,
                freshness_status,
                local_date_matches_target,
                source_authorized_for_settlement,
                persisted_surface_available,
                payload_json,
                recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(authority_id) DO UPDATE SET
                city=excluded.city,
                target_date=excluded.target_date,
                temperature_metric=excluded.temperature_metric,
                decision_time_utc=excluded.decision_time_utc,
                market_phase=excluded.market_phase,
                source=excluded.source,
                station_id=excluded.station_id,
                observation_time_utc=excluded.observation_time_utc,
                first_sample_time_utc=excluded.first_sample_time_utc,
                last_sample_time_utc=excluded.last_sample_time_utc,
                high_so_far=excluded.high_so_far,
                low_so_far=excluded.low_so_far,
                current_temp=excluded.current_temp,
                sample_count=excluded.sample_count,
                coverage_status=excluded.coverage_status,
                freshness_status=excluded.freshness_status,
                local_date_matches_target=excluded.local_date_matches_target,
                source_authorized_for_settlement=excluded.source_authorized_for_settlement,
                persisted_surface_available=excluded.persisted_surface_available,
                payload_json=excluded.payload_json,
                recorded_at=COALESCE(
                    settlement_day_observation_authority.recorded_at, excluded.recorded_at
                )
            """,
            (
                str(authority_id or "") or None,
                str(city or "") or None,
                str(target_date or "") or None,
                _metric,
                str(decision_time_utc or "") or None,
                str(market_phase or "") or None,
                str(source or "") or None,
                str(station_id or "") or None,
                str(observation_time_utc or "") or None,
                str(first_sample_time_utc or "") or None,
                str(last_sample_time_utc or "") or None,
                _coerce_optional_float(high_so_far),
                _coerce_optional_float(low_so_far),
                _coerce_optional_float(current_temp),
                int(sample_count) if sample_count is not None else None,
                str(coverage_status or "") or None,
                str(freshness_status or "") or None,
                _as_int_bool(local_date_matches_target),
                _as_int_bool(source_authorized_for_settlement),
                _as_int_bool(persisted_surface_available),
                payload_json,
                recorded_at,
            ),
        )
        if _owns_connection:
            _wconn.commit()
        return {"status": "written", "table": "settlement_day_observation_authority"}
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Failed to log settlement_day_observation_authority: %s", e
        )
        return {"status": "error", "table": "settlement_day_observation_authority", "error": str(e)}
    finally:
        if _owns_connection:
            _wconn.close()


def _coerce_optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _build_day0_context_json(candidate, decision) -> str | None:
    """Build the per-edge day0 observation-lock classification payload.

    OBS-AUTHORITY-FOUNDATION FIX-2 (2026-05-23). For settlement-day HIGH buy_yes
    edges, persists day0_truth_classification + observed high/low + candidate bin
    bounds + settlement-capture eligibility so an operator can tell whether a
    day0 edge is observation-locked, forecast-upside, or wrong. Returns None for
    rows where no day0 classification applies (non-HIGH, no edge, no observation,
    or the classifier returns None). Total + fail-soft — never raises.
    """
    try:
        edge = getattr(decision, "edge", None)
        observation = getattr(candidate, "observation", None)
        if edge is None or observation is None:
            return None
        from src.engine.evaluator import day0_high_truth_classification_for_edge

        classification = day0_high_truth_classification_for_edge(candidate, edge)
        if classification is None:
            return None

        edge_bin = getattr(edge, "bin", None)
        bin_low = _coerce_optional_float(getattr(edge_bin, "low", None))
        bin_high = _coerce_optional_float(getattr(edge_bin, "high", None))
        direction = str(getattr(edge, "direction", "") or "")
        observed_high = _coerce_optional_float(getattr(observation, "high_so_far", None))
        observed_low = _coerce_optional_float(getattr(observation, "low_so_far", None))
        current_temp = _coerce_optional_float(getattr(observation, "current_temp", None))
        obs_time = getattr(observation, "observation_time", None)
        obs_source = getattr(observation, "source", None)
        hours_remaining = _coerce_optional_float(getattr(candidate, "hours_to_resolution", None))

        # settlement_capture is reserved for facts already locked by canonical
        # observation. Eligible only when observation-locked AND buy_yes.
        eligible = classification == "observation_locked" and direction == "buy_yes"
        ineligible_reason = None
        if not eligible:
            ineligible_reason = f"classification={classification};direction={direction}"

        payload = {
            "day0_truth_classification": classification,
            "observed_high_so_far": observed_high,
            "observed_low_so_far": observed_low,
            "current_temp": current_temp,
            "candidate_bin_low": bin_low,
            "candidate_bin_high": bin_high,
            "observation_source": str(obs_source or "") or None,
            "observation_time": str(obs_time) if obs_time is not None else None,
            "hours_remaining": hours_remaining,
            "settlement_capture_eligible": bool(eligible),
            "settlement_capture_ineligible_reason": ineligible_reason,
            "observation_authority_id": str(getattr(decision, "observation_authority_id", "") or "") or None,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception as exc:  # noqa: BLE001 - durable telemetry is fail-soft.
        logger.warning("Failed to build day0_context_json: %s", exc)
        return None




def log_opportunity_fact(
    conn: sqlite3.Connection | None,  # Deprecated: ignored; function opens its own trade connection (INV-37 fix, wave-2)
    *,
    candidate,
    decision,
    should_trade: bool,
    rejection_stage: str,
    rejection_reasons: list[str] | None,
    recorded_at: str,
) -> dict:
    """Write one opportunity fact row to zeus_trades.db.

    INV-37 fix (wave-2, 2026-05-18): opens its own trade connection rather than
    accepting an opaque conn from callers. Pre-fix, callers could pass a
    forecasts-rooted or world-ATTACHed conn, causing writes to land in the wrong
    DB. Live cycles may pass their already-open trade connection; when its main
    DB path is verified as zeus_trades.db, reuse it to avoid a same-process
    second writer deadlocking on the cycle's own transaction.
    """
    _wconn = conn if _is_verified_trade_connection(conn) else get_trade_connection(write_class="live")
    _owns_connection = _wconn is not conn
    try:
        if not _table_exists(_wconn, "opportunity_fact"):
            logger.info("Opportunity fact table unavailable; skipping durable write")
            return {"status": "skipped_missing_table", "table": "opportunity_fact"}

        # OBS-AUTHORITY-FOUNDATION (2026-05-23): production zeus_trades.db has a
        # ghost opportunity_fact predating observation_authority_id (it is NOT
        # created by init_schema_trade_only's _TRADE_CLASS_DDL — see the table's
        # legacy_archived registry note). Idempotent backfill so the INSERT's new
        # column resolves. Cheap PRAGMA + at-most-once ALTER.
        _of_columns = _table_columns(_wconn, "opportunity_fact")
        if "observation_authority_id" not in _of_columns:
            try:
                _wconn.execute(
                    "ALTER TABLE opportunity_fact ADD COLUMN observation_authority_id TEXT;"
                )
            except sqlite3.OperationalError:
                pass  # concurrent add / already present
        if "day0_context_json" not in _of_columns:
            try:
                _wconn.execute(
                    "ALTER TABLE opportunity_fact ADD COLUMN day0_context_json TEXT;"
                )
            except sqlite3.OperationalError:
                pass  # concurrent add / already present

        edge = getattr(decision, "edge", None)
        direction = str(getattr(edge, "direction", "") or "unknown")
        if direction not in {"buy_yes", "buy_no", "unknown"}:
            direction = "unknown"
        range_label = str(getattr(getattr(edge, "bin", None), "label", "") or "")
        strategy_key = str(getattr(decision, "strategy_key", "") or "").strip() or None
        snapshot_id = str(getattr(decision, "decision_snapshot_id", "") or "").strip() or None
        p_raw = _decision_vector_value(decision, "p_raw")
        p_cal = _decision_vector_value(decision, "p_cal")
        p_market = _decision_vector_value(decision, "p_market")
        if p_cal is None and edge is not None:
            try:
                p_cal = float(getattr(edge, "p_model", None))
            except (TypeError, ValueError):
                p_cal = None
        if p_market is None and edge is not None:
            try:
                p_market = float(getattr(edge, "p_market", None))
            except (TypeError, ValueError):
                p_market = None
        best_edge = None
        ci_width = None
        alpha = getattr(decision, "alpha", None)
        if edge is not None:
            try:
                best_edge = float(getattr(edge, "edge", None))
            except (TypeError, ValueError):
                best_edge = None
            try:
                ci_width = max(0.0, float(edge.ci_upper) - float(edge.ci_lower))
            except (TypeError, ValueError, AttributeError):
                ci_width = None
        try:
            alpha = float(alpha) if alpha not in (None, "") else None
        except (TypeError, ValueError):
            alpha = None
        rejection_reason_json = None
        if rejection_reasons:
            rejection_reason_json = json.dumps(list(rejection_reasons), ensure_ascii=False)

        day0_context_json = _build_day0_context_json(candidate, decision)

        _wconn.execute(
            """
            INSERT INTO opportunity_fact (
                decision_id,
                candidate_id,
                city,
                target_date,
                range_label,
                direction,
                strategy_key,
                discovery_mode,
                entry_method,
                snapshot_id,
                p_raw,
                p_cal,
                p_market,
                alpha,
                best_edge,
                ci_width,
                rejection_stage,
                rejection_reason_json,
                availability_status,
                should_trade,
                observation_authority_id,
                day0_context_json,
                recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(decision_id) DO UPDATE SET
                candidate_id=excluded.candidate_id,
                city=excluded.city,
                target_date=excluded.target_date,
                range_label=excluded.range_label,
                direction=excluded.direction,
                strategy_key=excluded.strategy_key,
                discovery_mode=excluded.discovery_mode,
                entry_method=excluded.entry_method,
                snapshot_id=excluded.snapshot_id,
                p_raw=excluded.p_raw,
                p_cal=excluded.p_cal,
                p_market=excluded.p_market,
                alpha=excluded.alpha,
                best_edge=excluded.best_edge,
                ci_width=excluded.ci_width,
                rejection_stage=excluded.rejection_stage,
                rejection_reason_json=excluded.rejection_reason_json,
                availability_status=excluded.availability_status,
                should_trade=excluded.should_trade,
                observation_authority_id=COALESCE(
                    excluded.observation_authority_id, opportunity_fact.observation_authority_id
                ),
                day0_context_json=COALESCE(
                    excluded.day0_context_json, opportunity_fact.day0_context_json
                ),
                recorded_at=COALESCE(opportunity_fact.recorded_at, excluded.recorded_at)
            """,
            (
                str(getattr(decision, "decision_id", "") or ""),
                _opportunity_fact_candidate_id(candidate) or None,
                _candidate_city_name(candidate) or None,
                str(getattr(candidate, "target_date", "") or "") or None,
                range_label or None,
                direction,
                strategy_key,
                str(getattr(candidate, "discovery_mode", "") or "") or None,
                str(
                    getattr(decision, "selected_method", "")
                    or getattr(decision, "entry_method", "")
                    or ""
                )
                or None,
                snapshot_id,
                p_raw,
                p_cal,
                p_market,
                alpha,
                best_edge,
                ci_width,
                str(rejection_stage or "") or None,
                rejection_reason_json,
                _normalize_opportunity_availability_status(getattr(decision, "availability_status", "")),
                int(bool(should_trade)),
                str(getattr(decision, "observation_authority_id", "") or "") or None,
                day0_context_json,
                recorded_at,
            ),
        )
        if _owns_connection:
            _wconn.commit()
        return {"status": "written", "table": "opportunity_fact"}
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning("Failed to log opportunity fact: %s", e)
        return {"status": "error", "table": "opportunity_fact", "error": str(e)}
    finally:
        if _owns_connection:
            _wconn.close()



def log_availability_fact(
    conn: sqlite3.Connection | None,  # Deprecated: ignored; function opens its own world connection (INV-37 fix, PR-S4b §3)
    *,
    availability_id: str,
    scope_type: str,
    scope_key: str,
    failure_type: str,
    started_at: str,
    impact: str,
    details: dict | None = None,
    ended_at: str | None = None,
) -> dict:
    """Write one availability fact row to zeus-world.db.

    INV-37 fix (PR-S4b §3, 2026-05-18): opens its own world connection rather
    than accepting an opaque conn from callers. Pre-fix, cycle_runtime passed
    the trades-rooted conn, routing availability_fact rows to zeus_trades.db.
    The ``conn`` parameter is kept for backward compat but is no longer used.
    """
    conn = get_world_connection()
    try:
        if not _table_exists(conn, "availability_fact"):
            logger.info("Availability fact table unavailable; skipping durable write")
            return {"status": "skipped_missing_table", "table": "availability_fact"}

        normalized_scope_type = scope_type if scope_type in {"cycle", "candidate", "city_target", "order", "chain"} else "candidate"
        normalized_impact = impact if impact in {"skip", "degrade", "retry", "block"} else "skip"
        payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        conn.execute(
            """
            INSERT INTO availability_fact (
                availability_id,
                scope_type,
                scope_key,
                failure_type,
                started_at,
                ended_at,
                impact,
                details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(availability_id) DO UPDATE SET
                scope_type=excluded.scope_type,
                scope_key=excluded.scope_key,
                failure_type=excluded.failure_type,
                started_at=excluded.started_at,
                ended_at=excluded.ended_at,
                impact=excluded.impact,
                details_json=excluded.details_json
            """,
            (
                availability_id,
                normalized_scope_type,
                scope_key,
                failure_type,
                started_at,
                ended_at,
                normalized_impact,
                payload,
            ),
        )
        conn.commit()
        return {"status": "written", "table": "availability_fact"}
    finally:
        conn.close()


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601-ish timestamp string into a tz-AWARE datetime.

    Callers compare these timestamps with `>`/`<` across rows that may
    come from heterogeneous writers: runtime code uses
    datetime.now(timezone.utc).isoformat() (tz-aware), SQLite's built-in
    datetime('now') function returns "YYYY-MM-DD HH:MM:SS" with no tz
    (naive), and legacy writers sometimes used bare "Z" suffixes.
    Comparing a naive datetime with an aware one raises TypeError, which
    on 2026-04-11 crashed query_portfolio_loader_view every cycle after
    the nuke rebuild script left 7 naive timestamps in position_current.

    Contract: any value that parses at all is returned as UTC-aware. A
    naive input is assumed to already be UTC (zeus has no local-time
    writers — every producer is supposed to use UTC) and is upgraded
    by attaching tzinfo=timezone.utc. Invalid inputs return None.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Naive → assume UTC. Zeus's convention is UTC-everywhere; any
        # producer that writes naive is violating that convention and
        # the safer assumption is UTC over local time.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _execution_intent_id(*, trade_id: str, order_role: str, explicit_intent_id: str | None = None) -> str:
    if explicit_intent_id:
        return explicit_intent_id
    return f"{trade_id}:{order_role}"


def log_execution_fact(
    conn: sqlite3.Connection | None,
    *,
    intent_id: str,
    position_id: str,
    order_role: str,
    decision_id: str | None = None,
    command_id: str | None = None,
    strategy_key: str | None = None,
    posted_at: str | None = None,
    filled_at: str | None = None,
    voided_at: str | None = None,
    submitted_price: float | None = None,
    fill_price: float | None = None,
    shares: float | None = None,
    fill_quality: float | None = None,
    latency_seconds: float | None = None,
    venue_status: str | None = None,
    terminal_exec_status: str | None = None,
    clear_fill_fields: bool = False,
    posterior_id: int | None = None,
) -> dict:
    if conn is None:
        logger.info("Execution fact write skipped: no connection")
        return {"status": "skipped_no_connection", "table": "execution_fact"}
    if not _table_exists(conn, "execution_fact"):
        logger.info("Execution fact table unavailable; skipping durable write")
        return {"status": "skipped_missing_table", "table": "execution_fact"}

    from src.state.owner_routed_write import require_owner_main
    require_owner_main(conn, "execution_fact")  # bare-write helper: fail-closed unless conn is trade-rooted

    if order_role not in {"entry", "exit"}:
        raise ValueError(f"execution_fact order_role must be entry/exit, got {order_role!r}")

    # H2_E2E: posterior_id is an additive column; legacy DBs that have not yet run
    # the ALTER may lack it. Guard so the write stays robust either way (when the
    # column is absent the posterior link simply is not persisted — fail-soft).
    _exec_fact_cols = {row[1] for row in conn.execute("PRAGMA table_info(execution_fact)").fetchall()}
    _has_posterior_id = "posterior_id" in _exec_fact_cols

    current = conn.execute(
        """
        SELECT posted_at, filled_at, voided_at, submitted_price, fill_price, shares, fill_quality,
               latency_seconds, venue_status, terminal_exec_status, decision_id, strategy_key,
               command_id
        FROM execution_fact
        WHERE intent_id = ?
        """,
        (intent_id,),
    ).fetchone()

    stored_posted_at = posted_at or (current["posted_at"] if current else None)
    stored_voided_at = voided_at or (current["voided_at"] if current else None)
    stored_submitted_price = submitted_price if submitted_price is not None else (current["submitted_price"] if current else None)
    stored_venue_status = venue_status if venue_status not in (None, "") else (current["venue_status"] if current else None)
    stored_terminal_status = terminal_exec_status if terminal_exec_status not in (None, "") else (current["terminal_exec_status"] if current else None)
    stored_decision_id = decision_id if decision_id not in (None, "") else (current["decision_id"] if current else None)
    stored_strategy_key = strategy_key if strategy_key not in (None, "") else (current["strategy_key"] if current else None)
    stored_command_id = command_id if command_id not in (None, "") else (current["command_id"] if current else None)

    if clear_fill_fields:
        stored_filled_at = None
        stored_fill_price = None
        stored_shares = None
        stored_fill_quality = None
        stored_latency_seconds = None
        if terminal_exec_status in (None, ""):
            stored_terminal_status = "pending_fill_authority"
        if venue_status in (None, ""):
            stored_venue_status = stored_terminal_status
    else:
        stored_filled_at = filled_at or (current["filled_at"] if current else None)
        stored_fill_price = fill_price if fill_price is not None else (current["fill_price"] if current else None)
        stored_shares = shares if shares is not None else (current["shares"] if current else None)
        stored_fill_quality = fill_quality if fill_quality is not None else (current["fill_quality"] if current else None)
        if latency_seconds is None and stored_posted_at and stored_filled_at:
            posted_dt = _parse_iso_timestamp(stored_posted_at)
            filled_dt = _parse_iso_timestamp(stored_filled_at)
            if posted_dt is not None and filled_dt is not None:
                latency_seconds = max(0.0, (filled_dt - posted_dt).total_seconds())
        stored_latency_seconds = latency_seconds if latency_seconds is not None else (current["latency_seconds"] if current else None)

    _base_columns = [
        "intent_id",
        "position_id",
        "decision_id",
        "order_role",
        "strategy_key",
        "posted_at",
        "filled_at",
        "voided_at",
        "submitted_price",
        "fill_price",
        "shares",
        "fill_quality",
        "latency_seconds",
        "venue_status",
        "terminal_exec_status",
        "command_id",
    ]
    _base_values = [
        intent_id,
        position_id,
        stored_decision_id,
        order_role,
        stored_strategy_key,
        stored_posted_at,
        stored_filled_at,
        stored_voided_at,
        stored_submitted_price,
        stored_fill_price,
        stored_shares,
        stored_fill_quality,
        stored_latency_seconds,
        stored_venue_status,
        stored_terminal_status,
        stored_command_id,
    ]
    _update_clauses = [
        "position_id=excluded.position_id",
        "decision_id=excluded.decision_id",
        "order_role=excluded.order_role",
        "strategy_key=excluded.strategy_key",
        "posted_at=excluded.posted_at",
        "filled_at=excluded.filled_at",
        "voided_at=excluded.voided_at",
        "submitted_price=excluded.submitted_price",
        "fill_price=excluded.fill_price",
        "shares=excluded.shares",
        "fill_quality=excluded.fill_quality",
        "latency_seconds=excluded.latency_seconds",
        "venue_status=excluded.venue_status",
        "terminal_exec_status=excluded.terminal_exec_status",
        "command_id=COALESCE(excluded.command_id, execution_fact.command_id)",
    ]
    if _has_posterior_id:
        # H2_E2E: persist the fill->posterior link when the column exists. COALESCE
        # so a later reconcile pass that does not carry posterior_id never NULLs an
        # already-recorded link. NULL on every non-replacement order.
        _base_columns.append("posterior_id")
        _base_values.append(posterior_id)
        _update_clauses.append(
            "posterior_id=COALESCE(excluded.posterior_id, execution_fact.posterior_id)"
        )
    _placeholders = ", ".join("?" for _ in _base_columns)
    conn.execute(
        f"""
        INSERT INTO execution_fact ({", ".join(_base_columns)})
        VALUES ({_placeholders})
        ON CONFLICT(intent_id) DO UPDATE SET
            {", ".join(_update_clauses)}
        """,
        tuple(_base_values),
    )
    return {"status": "written", "table": "execution_fact"}


def _hours_between(started_at: str | None, ended_at: str | None) -> float | None:
    start_dt = _parse_iso_timestamp(started_at)
    end_dt = _parse_iso_timestamp(ended_at)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds() / 3600.0)


def log_outcome_fact(
    conn: sqlite3.Connection | None,
    *,
    position_id: str,
    strategy_key: str | None = None,
    entered_at: str | None = None,
    exited_at: str | None = None,
    settled_at: str | None = None,
    exit_reason: str | None = None,
    admin_exit_reason: str | None = None,
    decision_snapshot_id: str | None = None,
    pnl: float | None = None,
    outcome: int | None = None,
    hold_duration_hours: float | None = None,
    monitor_count: int | None = None,
    chain_corrections_count: int | None = None,
) -> dict:
    if conn is None:
        logger.info("Outcome fact write skipped: no connection")
        return {"status": "skipped_no_connection", "table": "outcome_fact"}
    if not _table_exists(conn, "outcome_fact"):
        logger.info("Outcome fact table unavailable; skipping durable write")
        return {"status": "skipped_missing_table", "table": "outcome_fact"}

    from src.state.owner_routed_write import require_owner_main
    require_owner_main(conn, "outcome_fact")  # bare-write helper: fail-closed unless conn is trade-rooted

    current = conn.execute(
        """
        SELECT entered_at, exited_at, settled_at, exit_reason, admin_exit_reason, decision_snapshot_id,
               pnl, outcome, hold_duration_hours, monitor_count, chain_corrections_count, strategy_key
        FROM outcome_fact
        WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()

    stored_entered_at = entered_at if entered_at not in (None, "") else (current["entered_at"] if current else None)
    stored_exited_at = exited_at if exited_at not in (None, "") else (current["exited_at"] if current else None)
    stored_settled_at = settled_at if settled_at not in (None, "") else (current["settled_at"] if current else None)
    stored_exit_reason = exit_reason if exit_reason not in (None, "") else (current["exit_reason"] if current else None)
    stored_admin_exit_reason = admin_exit_reason if admin_exit_reason not in (None, "") else (current["admin_exit_reason"] if current else None)
    stored_snapshot = decision_snapshot_id if decision_snapshot_id not in (None, "") else (current["decision_snapshot_id"] if current else None)
    stored_pnl = pnl if pnl is not None else (current["pnl"] if current else None)
    stored_outcome = outcome if outcome is not None else (current["outcome"] if current else None)
    stored_monitor_count = monitor_count if monitor_count is not None else (current["monitor_count"] if current else 0)
    stored_chain_corrections = chain_corrections_count if chain_corrections_count is not None else (current["chain_corrections_count"] if current else 0)
    stored_strategy_key = strategy_key if strategy_key not in (None, "") else (current["strategy_key"] if current else None)

    if hold_duration_hours is None:
        hold_duration_hours = _hours_between(
            stored_entered_at,
            stored_exited_at or stored_settled_at,
        )
    stored_hold_hours = hold_duration_hours if hold_duration_hours is not None else (current["hold_duration_hours"] if current else None)

    conn.execute(
        """
        INSERT INTO outcome_fact (
            position_id,
            strategy_key,
            entered_at,
            exited_at,
            settled_at,
            exit_reason,
            admin_exit_reason,
            decision_snapshot_id,
            pnl,
            outcome,
            hold_duration_hours,
            monitor_count,
            chain_corrections_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id) DO UPDATE SET
            strategy_key=excluded.strategy_key,
            entered_at=excluded.entered_at,
            exited_at=excluded.exited_at,
            settled_at=excluded.settled_at,
            exit_reason=excluded.exit_reason,
            admin_exit_reason=excluded.admin_exit_reason,
            decision_snapshot_id=excluded.decision_snapshot_id,
            pnl=excluded.pnl,
            outcome=excluded.outcome,
            hold_duration_hours=excluded.hold_duration_hours,
            monitor_count=excluded.monitor_count,
            chain_corrections_count=excluded.chain_corrections_count
        """,
        (
            position_id,
            stored_strategy_key,
            stored_entered_at,
            stored_exited_at,
            stored_settled_at,
            stored_exit_reason,
            stored_admin_exit_reason,
            stored_snapshot,
            stored_pnl,
            stored_outcome,
            stored_hold_hours,
            stored_monitor_count,
            stored_chain_corrections,
        ),
    )
    return {"status": "written", "table": "outcome_fact"}

_LEGACY_POSITION_EVENTS_COLUMNS = frozenset(
    {
        "runtime_trade_id",
        "position_state",
        "strategy",
        "source",
        "details_json",
        "timestamp",
        "env",
    }
)


def _guard_legacy_position_events_schema(conn: sqlite3.Connection) -> None:
    """Preserve loud failure for malformed legacy telemetry schemas.

    Canonical ``position_events`` is append/projection owned. These legacy
    telemetry helpers intentionally do not write to it, but tests still rely on
    them failing loudly when pointed at a malformed legacy runtime table instead
    of silently degrading.
    """

    if not _table_exists(conn, "position_events"):
        return
    columns = _table_columns(conn, "position_events")
    legacy_overlap = (_LEGACY_POSITION_EVENTS_COLUMNS - {"env"}) & columns
    if _table_exists(conn, "position_current"):
        if legacy_overlap:
            raise RuntimeError("hybrid position_events schema")
        return
    if not _LEGACY_POSITION_EVENTS_COLUMNS.issubset(columns):
        raise RuntimeError("legacy runtime position_events schema not installed")


def log_trade_entry(conn: sqlite3.Connection, pos) -> None:
    """Evidence spine: Log explicitly at entry for replay reconstruction.

    F5 demotion (2026-05-28): trade_decisions is audit-only legacy export.
    The live entry path no longer writes to trade_decisions; canonical
    truth lives in position_events / position_current.
    """
    if False: _ = pos.entry_method; _ = pos.selected_method  # Semantic Provenance Guard
    _guard_legacy_position_events_schema(conn)




def log_execution_report(conn: sqlite3.Connection, pos, result, *, decision_id: str | None = None) -> None:
    """Append an execution telemetry event tied to the runtime trade."""
    _guard_legacy_position_events_schema(conn)
    if not getattr(pos, "trade_id", ""):
        return
    submitted_price = getattr(result, "submitted_price", None)
    reported_fill_price = getattr(result, "fill_price", None)
    reported_shares = getattr(result, "shares", None)
    status = str(getattr(result, "status", "") or "")
    command_state = str(getattr(result, "command_state", "") or "")
    order_role = str(getattr(result, "order_role", "") or "entry")
    entry_fill_authority = order_role == "entry" and bool(
        getattr(pos, "has_fill_economics_authority", False)
    )
    fill_has_finality = (
        command_state == "FILLED"
        or bool(getattr(result, "filled_at", None))
        or entry_fill_authority
    )
    fill_price = reported_fill_price if fill_has_finality else None
    shares = reported_shares if fill_has_finality else None
    if entry_fill_authority:
        authority_price = _finite_float_or_zero(getattr(pos, "entry_price_avg_fill", None))
        authority_shares = _finite_float_or_zero(getattr(pos, "shares_filled", None))
        authority_cost = _finite_float_or_zero(getattr(pos, "filled_cost_basis_usd", None))
        if authority_price <= 0.0 and authority_cost > 0.0 and authority_shares > 0.0:
            authority_price = authority_cost / authority_shares
        if authority_price > 0.0:
            fill_price = authority_price
        if authority_shares > 0.0:
            shares = authority_shares
    fill_quality = None
    if fill_has_finality and fill_price not in (None, 0) and submitted_price not in (None, 0):
        try:
            fill_quality = (float(fill_price) - float(submitted_price)) / float(submitted_price)
        except (TypeError, ValueError, ZeroDivisionError):
            fill_quality = None
    if fill_quality is None and fill_has_finality:
        fill_quality = getattr(pos, "fill_quality", None)

    details = {
        "status": status,
        "reason": getattr(result, "reason", None),
        "submitted_price": submitted_price,
        "fill_price": fill_price,
        "reported_fill_price_ignored": (
            reported_fill_price if reported_fill_price not in (None, 0) and not fill_has_finality else None
        ),
        "shares": shares,
        "reported_shares_ignored": (
            reported_shares if reported_shares is not None and not fill_has_finality else None
        ),
        "timeout_seconds": getattr(result, "timeout_seconds", None),
        "fill_quality": fill_quality,
        "order_status": getattr(pos, "order_status", ""),
    }
    event_timestamp = (
        (getattr(result, "filled_at", None) if fill_has_finality else None)
        or getattr(pos, "order_posted_at", None)
        or datetime.now(timezone.utc).isoformat()
    )
    terminal_exec_status = status or None
    if not fill_has_finality and (
        status.lower() in {"filled", "confirmed"}
        or reported_fill_price not in (None, 0)
        or reported_shares is not None
        or not status
    ):
        terminal_exec_status = "pending_fill_authority"
    clear_fill_fields = not fill_has_finality
    voided_at = event_timestamp if status in {"rejected", "cancelled", "canceled"} else None
    posted_at = (
        getattr(pos, "order_posted_at", None)
        or getattr(result, "filled_at", None)
        or event_timestamp
    )
    log_execution_fact(
        conn,
        intent_id=_execution_intent_id(
            trade_id=getattr(pos, "trade_id", ""),
            order_role=order_role,
            explicit_intent_id=getattr(result, "intent_id", None),
        ),
        position_id=getattr(pos, "trade_id", ""),
        decision_id=decision_id,
        command_id=str(getattr(result, "command_id", None) or "") or None,
        order_role=order_role,
        strategy_key=str(getattr(pos, "strategy_key", "") or getattr(pos, "strategy", "") or "") or None,
        posted_at=posted_at,
        filled_at=getattr(result, "filled_at", None) if status == "filled" and fill_has_finality else None,
        voided_at=voided_at,
        submitted_price=submitted_price,
        fill_price=fill_price,
        shares=shares,
        fill_quality=fill_quality,
        venue_status=str(getattr(result, "venue_status", "") or getattr(pos, "order_status", "") or status or "") or None,
        terminal_exec_status=terminal_exec_status,
        clear_fill_fields=clear_fill_fields,
    )



def log_settlement_event(
    conn: sqlite3.Connection,
    pos,
    *,
    winning_bin: str,
    won: bool,
    outcome: int,
    exited_at_override: str | None = None,
) -> None:
    """Append a durable settlement event for learning/risk consumers."""
    _guard_legacy_position_events_schema(conn)
    settled_at = getattr(pos, "last_exit_at", None)
    entered_at = getattr(pos, "entered_at", None) or getattr(pos, "day0_entered_at", None)
    log_outcome_fact(
        conn,
        position_id=getattr(pos, "trade_id", ""),
        strategy_key=str(getattr(pos, "strategy_key", "") or getattr(pos, "strategy", "") or "") or None,
        entered_at=entered_at,
        exited_at=exited_at_override,
        settled_at=settled_at,
        exit_reason=getattr(pos, "exit_reason", None),
        admin_exit_reason=getattr(pos, "admin_exit_reason", None),
        decision_snapshot_id=getattr(pos, "decision_snapshot_id", None),
        pnl=getattr(pos, "pnl", None),
        outcome=outcome,
        monitor_count=int(getattr(pos, "monitor_count", 0) or 0),
        chain_corrections_count=int(getattr(pos, "chain_corrections_count", 0) or 0),
    )


def log_reconciled_entry_event(
    conn: sqlite3.Connection,
    pos,
    *,
    timestamp: str,
    details: dict | None = None,
) -> None:
    """Legacy reconciliation telemetry shim.

    Reconciled entry truth now flows through canonical lifecycle events. Keep
    this compatibility entry point as a schema guard plus no-op so old callers
    cannot silently write malformed legacy rows.
    """

    _ = (pos, timestamp, details)
    _guard_legacy_position_events_schema(conn)



def log_trade_exit(conn: sqlite3.Connection, pos) -> None:
    """Evidence spine: Update or insert exit fill evidence."""
    if False: _ = pos.entry_method; _ = pos.selected_method  # Semantic Provenance Guard
    try:
        from datetime import datetime
        env = getattr(pos, "env", "unknown_env") or "unknown_env"
        status = "voided" if getattr(pos, "state", "") == "voided" else "exited"
        snapshot_fk = _local_legacy_snapshot_fk(
            conn,
            getattr(pos, "decision_snapshot_id", None),
        )
        p_raw = getattr(pos, "p_raw", None)
        if p_raw is None:
            p_raw = getattr(pos, "p_posterior", 0.0)
        values = (
            pos.market_id, pos.bin_label, pos.direction, pos.size_usd, pos.entry_price, pos.last_exit_at or datetime.now(timezone.utc).isoformat(),
            snapshot_fk,
            getattr(pos, "calibration_version", "") or None,
            p_raw, getattr(pos, "p_posterior", None), pos.edge, 0.0, 0.0, 0.0,
            status, getattr(pos, "strategy", ""), pos.edge_source, _bin_type_for_label(pos.bin_label), env, pos.last_exit_at, pos.exit_price, getattr(pos, 'pnl', 0.0),
            getattr(pos, "trade_id", ""),
            getattr(pos, "order_id", ""),
            getattr(pos, "order_status", ""),
            getattr(pos, "order_posted_at", ""),
            getattr(pos, "entered_at", ""),
            getattr(pos, "chain_state", ""),
            getattr(pos, "discovery_mode", ""),
            getattr(pos, "market_hours_open", 0.0),
            getattr(pos, "fill_quality", 0.0),
            getattr(pos, "entry_method", ""),
            getattr(pos, "selected_method", ""),
            json.dumps(getattr(pos, "applied_validations", []) or []),
            getattr(pos, "exit_trigger", ""),
            getattr(pos, "exit_reason", ""),
            getattr(pos, "admin_exit_reason", ""),
            getattr(pos, "exit_divergence_score", 0.0),
            getattr(pos, "exit_market_velocity_1h", 0.0),
            getattr(pos, "exit_forward_edge", 0.0),
            getattr(pos, "settlement_semantics_json", None),
            getattr(pos, "epistemic_context_json", None),
            getattr(pos, "edge_context_json", None),
        )
        placeholders = ", ".join(["?"] * len(values))
        conn.execute(f"""
            INSERT INTO trade_decisions (
                market_id, bin_label, direction, size_usd, price, timestamp,
                forecast_snapshot_id, calibration_model_version,
                p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
                status, strategy, edge_source, bin_type, env, filled_at, fill_price, settlement_edge_usd,
                runtime_trade_id, order_id, order_status_text, order_posted_at, entered_at_ts, chain_state,
                discovery_mode, market_hours_open, fill_quality,
                entry_method, selected_method, applied_validations_json,
                exit_trigger, exit_reason, admin_exit_reason,
                exit_divergence_score, exit_market_velocity_1h, exit_forward_edge,
                settlement_semantics_json, epistemic_context_json, edge_context_json
            )
            VALUES ({placeholders})
        """, values)

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning('Failed to log trade exit: %s', e)


def update_trade_lifecycle(conn: sqlite3.Connection, pos) -> None:
    """Update the lifecycle state of the latest DB row for a runtime trade."""
    runtime_trade_id = getattr(pos, "trade_id", "")
    if not runtime_trade_id:
        return
    if not _table_exists(conn, "trade_decisions"):
        return

    row = conn.execute(
        """
        SELECT trade_id FROM trade_decisions
        WHERE runtime_trade_id = ?
        ORDER BY trade_id DESC
        LIMIT 1
        """,
        (runtime_trade_id,),
    ).fetchone()
    if row is None:
        # Bridge absent: attempt programmatic reconstruction via synthesizer.
        # This fires for pre-existing orphan rows (e.g. opening_inertia gap,
        # Karachi c30f28a5-d4e) on their first lifecycle event after the fix
        # ships.  If synthesis succeeds, lifecycle update proceeds normally.
        # If synthesis also fails, BridgeAbsentError surfaces the real defect.
        try:
            from src.state.trade_decisions_synthesizer import (
                BridgeSynthesisError,
                synthesize_missing_bridge,
            )
            synthesize_missing_bridge(conn, runtime_trade_id)
        except Exception as _synth_err:
            raise BridgeAbsentError(
                f"position {runtime_trade_id!r} has no trade_decisions row; "
                f"synthesizer also failed: {_synth_err!r}; cannot update lifecycle"
            ) from _synth_err
        # Re-query after successful synthesis
        row = conn.execute(
            """
            SELECT trade_id FROM trade_decisions
            WHERE runtime_trade_id = ?
            ORDER BY trade_id DESC
            LIMIT 1
            """,
            (runtime_trade_id,),
        ).fetchone()
        if row is None:
            raise BridgeAbsentError(
                f"position {runtime_trade_id!r} has no trade_decisions row even after "
                f"synthesis completed without error; cannot update lifecycle"
            )

    status = getattr(pos, "state", "") or "entered"
    timestamp = (
        getattr(pos, "day0_entered_at", "") if status == "day0_window" else ""
    ) or getattr(pos, "entered_at", "") or getattr(pos, "order_posted_at", "")
    filled_at = getattr(pos, "entered_at", "") if status in {"entered", "day0_window"} else None
    fill_price = getattr(pos, "entry_price", None) if status in {"entered", "day0_window"} else None
    entry_order_id = getattr(pos, "entry_order_id", "") or getattr(pos, "order_id", "")
    order_id = getattr(pos, "order_id", "") or entry_order_id
    bridge_economics = _trade_decisions_bridge_entry_economics(pos)
    bridge_cost_basis_usd = bridge_economics["cost_basis_usd"]
    bridge_entry_price = bridge_economics["entry_price"]
    conn.execute(
        """
        UPDATE trade_decisions
        SET status = ?,
            timestamp = COALESCE(NULLIF(?, ''), timestamp),
            size_usd = CASE
                WHEN COALESCE(size_usd, 0.0) <= 0.0 AND ? > 0.0 THEN ?
                ELSE size_usd
            END,
            price = CASE
                WHEN COALESCE(price, 0.0) <= 0.0 AND ? > 0.0 THEN ?
                ELSE price
            END,
            filled_at = COALESCE(?, filled_at),
            fill_price = COALESCE(?, fill_price),
            fill_quality = COALESCE(?, fill_quality),
            order_id = COALESCE(NULLIF(?, ''), order_id),
            order_status_text = COALESCE(NULLIF(?, ''), order_status_text),
            order_posted_at = COALESCE(NULLIF(?, ''), order_posted_at),
            entered_at_ts = COALESCE(NULLIF(?, ''), entered_at_ts),
            chain_state = COALESCE(NULLIF(?, ''), chain_state)
        WHERE trade_id = ?
        """,
        (
            status,
            timestamp,
            bridge_cost_basis_usd,
            bridge_cost_basis_usd,
            bridge_entry_price,
            bridge_entry_price,
            filled_at,
            fill_price,
            getattr(pos, "fill_quality", None),
            order_id,
            getattr(pos, "order_status", ""),
            getattr(pos, "order_posted_at", ""),
            getattr(pos, "entered_at", ""),
            getattr(pos, "chain_state", ""),
            row["trade_id"],
        ),
    )


def _trade_decisions_bridge_entry_economics(pos) -> dict[str, float]:
    """Best-effort audit bridge economics from canonical position state.

    `trade_decisions` is a legacy/audit bridge, not a money-path authority.
    When an existing bridge row was synthesized or rescued with size_usd=0,
    lifecycle sync may repair the display/audit cost from the canonical
    position/chain fields without fabricating edge, Kelly, or probability.
    """

    def _attr(name: str) -> object:
        try:
            return getattr(pos, name)
        except Exception:  # noqa: BLE001 - defensive against computed runtime props
            return None

    cost_basis_usd = _finite_float_or_zero(_attr("effective_cost_basis_usd"))
    if cost_basis_usd <= 0.0:
        for attr in ("chain_cost_basis_usd", "cost_basis_usd", "size_usd"):
            cost_basis_usd = _finite_float_or_zero(_attr(attr))
            if cost_basis_usd > 0.0:
                break

    shares = _finite_float_or_zero(_attr("effective_shares"))
    if shares <= 0.0:
        for attr in ("chain_shares", "shares"):
            shares = _finite_float_or_zero(_attr(attr))
            if shares > 0.0:
                break

    entry_price = _finite_float_or_zero(_attr("chain_avg_price"))
    if entry_price <= 0.0:
        entry_price = _finite_float_or_zero(_attr("entry_price"))
    if entry_price <= 0.0 and cost_basis_usd > 0.0 and shares > 0.0:
        entry_price = cost_basis_usd / shares

    return {
        "cost_basis_usd": cost_basis_usd,
        "entry_price": entry_price,
        "shares": shares,
    }




def _decode_position_event_rows(rows) -> list[dict]:
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        results.append(item)
    return results


def _is_missing_settlement_value(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _coerce_settlement_float(value) -> Optional[float]:
    if _is_missing_settlement_value(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_settlement_int(value) -> Optional[int]:
    if _is_missing_settlement_value(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _settlement_truth_ready(normalized: dict) -> bool:
    authority = str(normalized.get("settlement_authority") or "").strip().upper()
    source = str(normalized.get("settlement_truth_source") or "").strip()
    metric = str(normalized.get("settlement_temperature_metric") or "").strip().lower()
    return (
        authority == "VERIFIED"
        and source in SETTLEMENT_METRIC_READY_TRUTH_SOURCES
        and metric in {"high", "low"}
        and normalized.get("settlement_value") is not None
    )




def _normalize_position_settlement_event(event: dict) -> Optional[dict]:
    details = dict(event.get("details") or {})
    contract_missing_fields = [
        field
        for field in CANONICAL_POSITION_SETTLED_DETAIL_FIELDS
        if _is_missing_settlement_value(details.get(field))
    ]
    normalized = {
        "trade_id": str(event.get("runtime_trade_id") or ""),
        "city": str(event.get("city") or ""),
        "target_date": str(event.get("target_date") or ""),
        "range_label": str(event.get("bin_label") or ""),
        "direction": str(event.get("direction") or ""),
        "p_posterior": _coerce_settlement_float(details.get("p_posterior")),
        "outcome": _coerce_settlement_int(details.get("outcome")),
        "pnl": _coerce_settlement_float(details.get("pnl")),
        "decision_snapshot_id": str(event.get("decision_snapshot_id") or ""),
        "edge_source": str(event.get("edge_source") or ""),
        "strategy": str(event.get("strategy") or ""),
        "settled_at": str(event.get("timestamp") or ""),
        "winning_bin": details.get("winning_bin"),
        "position_bin": details.get("position_bin") or event.get("bin_label"),
        "won": details.get("won"),
        "exit_price": _coerce_settlement_float(details.get("exit_price")),
        "exit_reason": str(details.get("exit_reason") or ""),
        "settlement_authority": str(details.get("settlement_authority") or "UNKNOWN").upper(),
        "settlement_truth_source": str(details.get("settlement_truth_source") or ""),
        "settlement_market_slug": str(details.get("settlement_market_slug") or ""),
        "settlement_temperature_metric": str(details.get("settlement_temperature_metric") or ""),
        "settlement_source": str(details.get("settlement_source") or ""),
        "settlement_value": _coerce_settlement_float(details.get("settlement_value")),
        "env": str(event.get("env") or ""),
        "source": "position_events",
        "authority_level": "durable_event",
        "contract_version": str(
            details.get("contract_version") or CANONICAL_POSITION_SETTLED_CONTRACT_VERSION
        ),
    }
    missing_required = [
        field
        for field in AUTHORITATIVE_SETTLEMENT_ROW_REQUIRED_FIELDS
        if _is_missing_settlement_value(normalized.get(field))
    ]
    if missing_required:
        normalized.update({
            "is_degraded": True,
            "degraded_reason": f"missing_required_fields:{','.join(missing_required)}",
            "contract_missing_fields": contract_missing_fields,
            "canonical_payload_complete": not contract_missing_fields,
            "learning_snapshot_ready": False,
            "metric_ready": False,
            "authority_level": "durable_event_malformed",
            "required_missing_fields": missing_required,
        })
        return normalized

    degraded_reasons: list[str] = []
    if contract_missing_fields:
        degraded_reasons.append(
            f"missing_payload_fields:{','.join(contract_missing_fields)}"
        )
    if not normalized["decision_snapshot_id"]:
        degraded_reasons.append("missing_decision_snapshot_id")
    truth_ready = _settlement_truth_ready(normalized)
    if not truth_ready:
        degraded_reasons.append("missing_verified_settlement_truth")
    normalized.update({
        "is_degraded": bool(degraded_reasons),
        "degraded_reason": "; ".join(degraded_reasons),
        "contract_missing_fields": contract_missing_fields,
        "canonical_payload_complete": not contract_missing_fields,
        "learning_snapshot_ready": bool(normalized["decision_snapshot_id"]) and truth_ready,
        "metric_ready": truth_ready,
        "required_missing_fields": [],
    })
    return normalized


def query_position_events(conn: sqlite3.Connection, runtime_trade_id: str, limit: int = 50) -> list[dict]:
    """Load recent canonical position events for one position."""
    rows = conn.execute(
        """
        SELECT event_type,
               position_id AS runtime_trade_id,
               NULL AS position_state,
               order_id,
               snapshot_id AS decision_snapshot_id,
               NULL AS city,
               NULL AS target_date,
               NULL AS market_id,
               NULL AS bin_label,
               NULL AS direction,
               strategy_key AS strategy,
               NULL AS edge_source,
               source_module AS source,
               payload_json AS details_json,
               occurred_at AS timestamp,
               env
        FROM position_events
        WHERE position_id = ?
        ORDER BY sequence_no ASC
        LIMIT ?
        """,
        (runtime_trade_id, limit),
    ).fetchall()
    return _decode_position_event_rows(rows)


def query_settlement_events(
    conn: sqlite3.Connection,
    limit: int | None = 50,
    *,
    city: str | None = None,
    target_date: str | None = None,
    env: str | None = None,
    not_before: str | None = None,
) -> list[dict]:
    """Load recent canonical SETTLED events from the durable event spine."""
    from src.state.projection import normalize_position_event_env

    query_env = normalize_position_event_env(env, default=get_mode())
    event_filters = ["event_type = 'SETTLED'", "env = ?"]
    event_params: list[object] = [query_env]
    filters: list[str] = []
    params: list[object] = []
    if city is not None:
        filters.append("pc.city = ?")
        params.append(city)
    if target_date is not None:
        filters.append("pc.target_date = ?")
        params.append(target_date)
    if not_before is not None:
        event_filters.append("occurred_at >= ?")
        event_params.append(not_before)
    event_where_clause = " AND ".join(event_filters)
    where_clause = " AND ".join(["rn = 1", *filters])
    query = f"""
        SELECT e.event_type,
               e.position_id AS runtime_trade_id,
               NULL AS position_state,
               e.order_id,
               e.snapshot_id AS decision_snapshot_id,
               pc.city,
               pc.target_date,
               pc.market_id,
               pc.bin_label,
               pc.direction,
               e.strategy_key AS strategy,
               pc.edge_source,
               e.source_module AS source,
               e.payload_json AS details_json,
               e.occurred_at AS timestamp,
               e.env
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY position_id ORDER BY sequence_no DESC) AS rn
            FROM position_events
            WHERE {event_where_clause}
        ) e
        LEFT JOIN position_current pc ON pc.position_id = e.position_id
        WHERE {where_clause}
        ORDER BY e.occurred_at DESC
        """
    params = event_params + params
    if limit is not None:
        query += "\n        LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return _decode_position_event_rows(rows)


def query_authoritative_settlement_rows(
    conn: sqlite3.Connection,
    limit: int | None = 50,
    *,
    city: str | None = None,
    target_date: str | None = None,
    env: str | None = None,
    not_before: str | None = None,
) -> list[dict]:
    """Return stage-level settlement events only.

    ``env`` gates canonical ``position_events`` rows. Missing canonical env is
    not live authority.
    """
    stage_events = []
    if _table_exists(conn, "position_events") and _table_exists(conn, "position_current"):
        stage_events = query_settlement_events(
            conn,
            limit=limit,
            city=city,
            target_date=target_date,
            env=env,
            not_before=not_before,
        )
    normalized_stage = [
        normalized
        for event in stage_events
        if (normalized := _normalize_position_settlement_event(event)) is not None
    ]
    if normalized_stage:
        return normalized_stage[:limit] if limit is not None else normalized_stage

    return []


def query_authoritative_settlement_source(conn: sqlite3.Connection) -> str:
    """Report which settlement source is currently authoritative for readers."""
    rows = query_authoritative_settlement_rows(conn, limit=1)
    if not rows:
        return "none"
    return str(rows[0].get("source") or "none")


def refresh_strategy_health(
    conn: sqlite3.Connection | None,
    *,
    as_of: str | None = None,
) -> dict:
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "table": "strategy_health",
            "rows_written": 0,
        }
    if not _table_exists(conn, "strategy_health"):
        return {
            "status": "skipped_missing_table",
            "table": "strategy_health",
            "rows_written": 0,
        }

    required_tables = ("position_current",)
    optional_tables = ("outcome_fact", "execution_fact", "risk_actions")
    missing_required_tables = [table for table in required_tables if not _table_exists(conn, table)]
    missing_optional_tables = [table for table in optional_tables if not _table_exists(conn, table)]
    settlement_authority_missing_tables = []
    if not _table_exists(conn, "position_events"):
        settlement_authority_missing_tables.append("position_events")
        if not _table_exists(conn, "decision_log"):
            settlement_authority_missing_tables.append("decision_log")
    refresh_time = as_of or datetime.now(timezone.utc).isoformat()
    if missing_required_tables:
        return {
            "status": "skipped_missing_inputs",
            "table": "strategy_health",
            "rows_written": 0,
            "as_of": refresh_time,
            "missing_required_tables": missing_required_tables,
            "missing_optional_tables": missing_optional_tables,
            "settlement_authority_missing_tables": settlement_authority_missing_tables,
            "omitted_fields": [
                "risk_level",
                "brier_30d",
                "edge_trend_30d",
            ],
        }

    position_view = query_position_current_status_view(conn)
    position_metrics: dict[str, dict[str, float]] = {}
    omitted_noncanonical_strategy_counts = {
        "position_current": 0,
        "settlement": 0,
        "execution_fact": 0,
        "risk_actions": 0,
    }
    for position in position_view.get("positions", []):
        strategy_key = str(position.get("strategy") or "unclassified")
        if strategy_key not in CANONICAL_STRATEGY_KEYS:
            omitted_noncanonical_strategy_counts["position_current"] += 1
            continue
        bucket = position_metrics.setdefault(
            strategy_key,
            {
                "open_exposure_usd": 0.0,
                "unrealized_pnl": 0.0,
            },
        )
        bucket["open_exposure_usd"] += float(
            position.get("effective_cost_basis_usd")
            if position.get("effective_cost_basis_usd") is not None
            else position.get("size_usd", 0.0)
            or 0.0
        )
        bucket["unrealized_pnl"] += float(position.get("unrealized_pnl", 0.0) or 0.0)
    position_metrics = {
        strategy_key: {
            "open_exposure_usd": round(float(bucket.get("open_exposure_usd", 0.0) or 0.0), 2),
            "unrealized_pnl": round(float(bucket.get("unrealized_pnl", 0.0) or 0.0), 2),
        }
        for strategy_key, bucket in position_metrics.items()
    }

    settled_cutoff = _shift_iso_timestamp(refresh_time, days=30)
    settled_cutoff_dt = _parse_iso_timestamp(settled_cutoff)
    settlement_metrics: dict[str, dict] = {}
    settlement_rows = query_authoritative_settlement_rows(conn, limit=None)
    settlement_degraded_rows = 0
    for settlement_row in settlement_rows:
        if settlement_row.get("is_degraded", False):
            settlement_degraded_rows += 1
        if not settlement_row.get("metric_ready", False):
            continue
        settled_at = str(settlement_row.get("settled_at") or "")
        settled_at_dt = _parse_iso_timestamp(settled_at)
        if not settled_at:
            continue
        if settled_cutoff_dt is not None:
            if settled_at_dt is None or settled_at_dt < settled_cutoff_dt:
                continue
        elif settled_at < settled_cutoff:
            continue
        strategy_key = str(settlement_row.get("strategy") or "unclassified")
        if strategy_key not in CANONICAL_STRATEGY_KEYS:
            omitted_noncanonical_strategy_counts["settlement"] += 1
            continue
        bucket = settlement_metrics.setdefault(
            strategy_key,
            {
                "settled_trades_30d": 0,
                "realized_pnl_30d": 0.0,
                "wins": 0,
            },
        )
        bucket["settled_trades_30d"] += 1
        bucket["realized_pnl_30d"] += float(settlement_row.get("pnl") or 0.0)
        if int(settlement_row.get("outcome") or 0) == 1:
            bucket["wins"] += 1
    settlement_metrics = {
        strategy_key: {
            "settled_trades_30d": int(bucket["settled_trades_30d"]),
            "realized_pnl_30d": round(float(bucket["realized_pnl_30d"]), 2),
            "win_rate_30d": round(float(bucket["wins"]) / int(bucket["settled_trades_30d"]), 4)
            if int(bucket["settled_trades_30d"])
            else None,
        }
        for strategy_key, bucket in settlement_metrics.items()
    }

    execution_cutoff = _shift_iso_timestamp(refresh_time, days=14)
    execution_metrics: dict[str, dict] = {}
    if "execution_fact" not in missing_optional_tables:
        execution_rows = conn.execute(
            """
            SELECT
                strategy_key,
                SUM(CASE WHEN terminal_exec_status = 'filled' THEN 1 ELSE 0 END) AS filled,
                SUM(CASE WHEN terminal_exec_status IN ('rejected', 'cancelled', 'canceled') THEN 1 ELSE 0 END) AS rejected
            FROM execution_fact
            WHERE order_role = 'entry'
              AND COALESCE(filled_at, voided_at, posted_at) IS NOT NULL
              AND COALESCE(filled_at, voided_at, posted_at) >= ?
            GROUP BY strategy_key
            """,
            (execution_cutoff,),
        ).fetchall()
        for row in execution_rows:
            strategy_key = str(row["strategy_key"] or "unclassified")
            if strategy_key not in CANONICAL_STRATEGY_KEYS:
                omitted_noncanonical_strategy_counts["execution_fact"] += 1
                continue
            filled = int(row["filled"] or 0)
            rejected = int(row["rejected"] or 0)
            observed = filled + rejected
            fill_rate = round(filled / observed, 4) if observed else None
            execution_metrics[strategy_key] = {
                "fill_rate_14d": fill_rate,
                "execution_decay_flag": int(fill_rate is not None and observed >= 10 and fill_rate < 0.3),
            }

    risk_action_metrics: dict[str, dict] = {}
    if "risk_actions" not in missing_optional_tables:
        risk_action_rows = conn.execute(
            """
            SELECT strategy_key, action_type, reason
            FROM risk_actions
            WHERE status = 'active'
              AND (effective_until IS NULL OR effective_until > ?)
              AND issued_at <= ?
            """,
            (refresh_time, refresh_time),
        ).fetchall()
        for row in risk_action_rows:
            strategy_key = str(row["strategy_key"] or "")
            if not strategy_key:
                continue
            if strategy_key not in CANONICAL_STRATEGY_KEYS:
                omitted_noncanonical_strategy_counts["risk_actions"] += 1
                continue
            bucket = risk_action_metrics.setdefault(
                strategy_key,
                {
                    "edge_compression_flag": 0,
                    "execution_decay_flag": 0,
                },
            )
            reason = str(row["reason"] or "")
            if "edge_compression" in reason:
                bucket["edge_compression_flag"] = 1
            if "execution_decay(" in reason:
                bucket["execution_decay_flag"] = 1

    strategy_keys = set(position_metrics)
    strategy_keys.update(settlement_metrics)
    strategy_keys.update(execution_metrics)
    strategy_keys.update(risk_action_metrics)

    conn.execute("DELETE FROM strategy_health")
    rows_written = 0
    for strategy_key in sorted(strategy_keys):
        position_bucket = position_metrics.get(strategy_key, {})
        settlement_bucket = settlement_metrics.get(strategy_key, {})
        execution_bucket = execution_metrics.get(strategy_key, {})
        action_bucket = risk_action_metrics.get(strategy_key, {})
        conn.execute(
            """
            INSERT INTO strategy_health (
                strategy_key,
                as_of,
                open_exposure_usd,
                settled_trades_30d,
                realized_pnl_30d,
                unrealized_pnl,
                win_rate_30d,
                brier_30d,
                fill_rate_14d,
                edge_trend_30d,
                risk_level,
                execution_decay_flag,
                edge_compression_flag
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?)
            """,
            (
                strategy_key,
                refresh_time,
                float(position_bucket.get("open_exposure_usd", 0.0)),
                int(settlement_bucket.get("settled_trades_30d", 0)),
                float(settlement_bucket.get("realized_pnl_30d", 0.0)),
                float(position_bucket.get("unrealized_pnl", 0.0)),
                settlement_bucket.get("win_rate_30d"),
                execution_bucket.get("fill_rate_14d"),
                int(
                    max(
                        int(execution_bucket.get("execution_decay_flag", 0)),
                        int(action_bucket.get("execution_decay_flag", 0)),
                    )
                ),
                int(action_bucket.get("edge_compression_flag", 0)),
            ),
        )
        rows_written += 1
    settlement_authority_degraded = bool(
        settlement_authority_missing_tables or settlement_degraded_rows
    )
    if rows_written:
        refresh_status = "refreshed_degraded" if settlement_authority_degraded else "refreshed"
    else:
        refresh_status = "refreshed_empty_degraded" if settlement_authority_degraded else "refreshed_empty"
    return {
        "status": refresh_status,
        "table": "strategy_health",
        "rows_written": rows_written,
        "as_of": refresh_time,
        "missing_required_tables": missing_required_tables,
        "missing_optional_tables": missing_optional_tables,
        "settlement_authority_missing_tables": settlement_authority_missing_tables,
        "settlement_degraded_rows": settlement_degraded_rows,
        "omitted_fields": [
            "risk_level",
            "brier_30d",
            "edge_trend_30d",
        ],
        "omitted_noncanonical_strategy_counts": omitted_noncanonical_strategy_counts,
    }


def query_strategy_health_snapshot(
    conn: sqlite3.Connection | None,
    *,
    now: str | None = None,
    max_age_seconds: int = 300,
) -> dict:
    snapshot_time = now or datetime.now(timezone.utc).isoformat()
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "table": "strategy_health",
            "by_strategy": {},
            "stale_strategy_keys": [],
        }
    if not _table_exists(conn, "strategy_health"):
        return {
            "status": "missing_table",
            "table": "strategy_health",
            "by_strategy": {},
            "stale_strategy_keys": [],
        }
    rows = conn.execute(
        """
        SELECT sh.*
        FROM strategy_health sh
        JOIN (
            SELECT strategy_key, MAX(as_of) AS latest_as_of
            FROM strategy_health
            GROUP BY strategy_key
        ) latest
          ON latest.strategy_key = sh.strategy_key
         AND latest.latest_as_of = sh.as_of
        ORDER BY sh.strategy_key
        """
    ).fetchall()
    if not rows:
        return {
            "status": "empty",
            "table": "strategy_health",
            "by_strategy": {},
            "stale_strategy_keys": [],
        }

    snapshot_dt = _parse_iso_timestamp(snapshot_time)
    stale_strategy_keys: list[str] = []
    by_strategy: dict[str, dict] = {}
    for row in rows:
        strategy_key = str(row["strategy_key"])
        as_of_raw = str(row["as_of"] or "")
        row_as_of = _parse_iso_timestamp(as_of_raw)
        age_seconds = None
        if snapshot_dt is not None and row_as_of is not None:
            age_seconds = max(0.0, (snapshot_dt - row_as_of).total_seconds())
        if age_seconds is None or _freshness_registry.evaluate("strategy_health", age_seconds, override_threshold_seconds=max_age_seconds) >= FreshnessLevel.STALE:
            stale_strategy_keys.append(strategy_key)
        by_strategy[strategy_key] = {
            key: row[key]
            for key in row.keys()
        }
        by_strategy[strategy_key]["age_seconds"] = age_seconds

    return {
        "status": "stale" if stale_strategy_keys else "fresh",
        "table": "strategy_health",
        "as_of": max(str(row["as_of"] or "") for row in rows),
        "by_strategy": by_strategy,
        "stale_strategy_keys": stale_strategy_keys,
        "max_age_seconds": max_age_seconds,
    }


def query_position_current_status_view(conn: sqlite3.Connection | None) -> dict:
    # PR D0 (Finding D0, Part-2 audit, 2026-05-27): use has_verified_trade_fill
    # for the unverified_entries FILL-ECONOMICS count instead of entry_fill_verified.
    # entry_fill_verified is now False for balance-only rescue positions (the rescue
    # branch no longer sets it True), so the helper correctly discriminates.
    from src.state.portfolio import has_verified_trade_fill as _has_verified_trade_fill  # noqa: PLC0415

    if conn is None:
        return {
            "status": "skipped_no_connection",
            "table": "position_current",
            "positions": [],
            "strategy_open_counts": {},
            "open_positions": 0,
            "total_exposure_usd": 0.0,
            "unrealized_pnl": 0.0,
            "chain_state_counts": {},
            "exit_state_counts": {},
            "unverified_entries": 0,
            "day0_positions": 0,
        }
    if not _table_exists(conn, "position_current"):
        return {
            "status": "missing_table",
            "table": "position_current",
            "positions": [],
            "strategy_open_counts": {},
            "open_positions": 0,
            "total_exposure_usd": 0.0,
            "unrealized_pnl": 0.0,
            "chain_state_counts": {},
            "exit_state_counts": {},
            "unverified_entries": 0,
            "day0_positions": 0,
        }

    rows = conn.execute(
        """
        SELECT position_id, phase, trade_id, city, bin_label, direction,
               size_usd, shares, cost_basis_usd, entry_price,
               strategy_key, chain_state, order_status,
               decision_snapshot_id, last_monitor_market_price,
               token_id, no_token_id, condition_id,
               fill_authority,
               chain_shares, chain_avg_price, chain_cost_basis_usd
        FROM position_current
        ORDER BY updated_at DESC, position_id
        """
    ).fetchall()
    trade_ids = [str(row["trade_id"] or row["position_id"] or "") for row in rows]
    transitional_hints = _query_transitional_position_hints(conn, trade_ids)
    fill_hints = _query_entry_execution_fill_hints(conn, trade_ids)

    positions: list[dict] = []
    strategy_open_counts: dict[str, int] = {}
    chain_state_counts: dict[str, int] = {}
    exit_state_counts: dict[str, int] = {}
    total_exposure_usd = 0.0
    total_unrealized_pnl = 0.0
    unverified_entries = 0
    day0_positions = 0

    for row in rows:
        phase = str(row["phase"] or "")
        if phase not in OPEN_EXPOSURE_PHASES:
            continue
        trade_id = str(row["trade_id"] or row["position_id"] or "")
        hints = transitional_hints.get(trade_id, {})
        fill_economics = _position_current_effective_entry_economics(
            row,
            fill_hints.get(trade_id),
        )
        chain_state = str(row["chain_state"] or "unknown")
        exit_state = str(hints.get("exit_state") or "none")
        if phase != "pending_exit":
            exit_state = "none"
        entry_fill_verified = bool(
            hints.get("entry_fill_verified", False)
            or fill_economics["entry_fill_verified"]
        )
        admin_exit_reason = str(hints.get("admin_exit_reason") or "")
        day0_entered_at = str(hints.get("day0_entered_at") or "")
        shares = float(fill_economics["effective_shares"] or 0.0)
        mark_price = row["last_monitor_market_price"]
        cost_basis_usd = fill_economics["pnl_cost_basis_usd"]
        unrealized_pnl = 0.0
        if shares and mark_price is not None and cost_basis_usd is not None:
            unrealized_pnl = round((shares * float(mark_price)) - float(cost_basis_usd), 2)

        positions.append(
            {
                "trade_id": trade_id,
                "city": str(row["city"] or ""),
                "direction": str(row["direction"] or ""),
                "strategy": str(row["strategy_key"] or ""),
                "state": phase,
                "chain_state": chain_state,
                "exit_state": exit_state,
                "entry_fill_verified": entry_fill_verified,
                "admin_exit_reason": admin_exit_reason,
                "size_usd": float(fill_economics["effective_cost_basis_usd"] or 0.0),
                "submitted_size_usd": float(fill_economics["submitted_size_usd"] or 0.0),
                "effective_cost_basis_usd": float(fill_economics["effective_cost_basis_usd"] or 0.0),
                "entry_economics_authority": fill_economics["entry_economics_authority"],
                "fill_authority": fill_economics["fill_authority"],
                "entry_economics_source": fill_economics["entry_economics_source"],
                "entry_price_avg_fill": float(fill_economics["entry_price_avg_fill"] or 0.0),
                "shares_filled": float(fill_economics["shares_filled"] or 0.0),
                "filled_cost_basis_usd": float(fill_economics["filled_cost_basis_usd"] or 0.0),
                "execution_fact_intent_id": fill_economics["execution_fact_intent_id"],
                "execution_fact_filled_at": fill_economics["execution_fact_filled_at"],
                "shares": shares,
                "entry_price": fill_economics["effective_entry_price"],
                "edge": None,
                "bin_label": str(row["bin_label"] or ""),
                "decision_snapshot_id": str(row["decision_snapshot_id"] or ""),
                "token_id": str(row["token_id"] or ""),
                "no_token_id": str(row["no_token_id"] or ""),
                "condition_id": str(row["condition_id"] or ""),
                "day0_entered_at": day0_entered_at,
                "mark_price": mark_price,
                "unrealized_pnl": unrealized_pnl,
            }
        )

        strategy_key = str(row["strategy_key"] or "unclassified")
        strategy_open_counts[strategy_key] = strategy_open_counts.get(strategy_key, 0) + 1
        chain_state_counts[chain_state] = chain_state_counts.get(chain_state, 0) + 1
        exit_state_counts[exit_state] = exit_state_counts.get(exit_state, 0) + 1
        total_exposure_usd += float(fill_economics["effective_cost_basis_usd"] or 0.0)
        total_unrealized_pnl += unrealized_pnl
        # PR D0: count unverified entries by fill_authority from position_current,
        # not by entry_fill_verified (which is now False for balance-only rescue).
        # A row lacking fill_authority (legacy NULL) falls through to the else-branch
        # of _has_verified_trade_fill, which returns False — fail-closed.
        row_fill_authority = str(row["fill_authority"] or "").strip()
        if not _has_verified_trade_fill({"fill_authority": row_fill_authority}):
            unverified_entries += 1
        if phase == "day0_window":
            day0_positions += 1

    return {
        "status": "ok",
        "table": "position_current",
        "positions": positions,
        "strategy_open_counts": strategy_open_counts,
        "open_positions": len(positions),
        "total_exposure_usd": round(total_exposure_usd, 2),
        "unrealized_pnl": round(total_unrealized_pnl, 2),
        "chain_state_counts": chain_state_counts,
        "exit_state_counts": exit_state_counts,
        "unverified_entries": unverified_entries,
        "day0_positions": day0_positions,
    }


def _latest_position_event_envs(
    conn: sqlite3.Connection,
    position_ids: list[str],
) -> dict[str, str]:
    if not position_ids:
        return {}
    if not _table_exists(conn, "position_events"):
        return {}
    if "env" not in _table_columns(conn, "position_events"):
        return {}
    placeholders = ", ".join(["?"] * len(position_ids))
    rows = conn.execute(
        f"""
        SELECT position_id, env
        FROM (
            SELECT position_id,
                   env,
                   ROW_NUMBER() OVER (
                       PARTITION BY position_id
                       ORDER BY sequence_no DESC
                   ) AS rn
            FROM position_events
            WHERE position_id IN ({placeholders})
        )
        WHERE rn = 1
        """,
        tuple(position_ids),
    ).fetchall()
    envs: dict[str, str] = {}
    for row in rows:
        env = str(row["env"] or "").strip().lower()
        if env in POSITION_EVENT_ENVS:
            envs[str(row["position_id"])] = env
    return envs


def query_portfolio_loader_view(conn: sqlite3.Connection | None, *, temperature_metric: str | None = None) -> dict:
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "table": "position_current",
            "positions": [],
            "temperature_metric": temperature_metric,
        }
    if not _table_exists(conn, "position_current"):
        return {
            "status": "missing_table",
            "table": "position_current",
            "positions": [],
            "temperature_metric": temperature_metric,
        }

    actual_cols = {row[1] for row in conn.execute("PRAGMA table_info(position_current)").fetchall()}
    if "temperature_metric" not in actual_cols:
        raise RuntimeError(
            "position_current.temperature_metric column missing; "
            "init_schema ALTER must have failed. Re-run init or check DB integrity."
        )

    where_clause = ""
    params: tuple = ()
    if temperature_metric is not None:
        where_clause = "WHERE temperature_metric = ?"
        params = (temperature_metric,)

    position_current_env_expr = (
        "env"
        if "env" in actual_cols
        else "NULL AS env"
    )

    # PR #352 (Part-3/Part-5 audit Finding 1, 2026-05-27): the D0b durable
    # authority columns must round-trip through the loader, else a chain-synced
    # position loses chain_verified_at on restart and classify_chain_state()
    # mis-reads it as CHAIN_UNKNOWN — blocking legitimate void. Guarded by
    # column presence (legacy DBs pre-D0b project NULL) like the env expr above.
    _authority_cols = (
        "fill_authority",
        "recovery_authority",
        "chain_shares",
        # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain-observed
        # economics columns round-trip through the loader.
        "chain_avg_price",
        "chain_cost_basis_usd",
        "chain_seen_at",
        "chain_absence_at",
    )
    authority_select_expr = ", ".join(
        c if c in actual_cols else f"NULL AS {c}" for c in _authority_cols
    )
    # These runtime columns are additive on live DBs. Keep the loader read
    # boundary compatible with older/partially migrated DBs, but always project
    # the keys it later maps into Position so load_portfolio cannot fail after
    # restart.
    _runtime_cols_defaults = {
        "entry_ci_width": "0.0",
        "exit_retry_count": "0",
        "next_exit_retry_at": "NULL",
        "exit_reason": "NULL",
        "admin_exit_reason": "NULL",
        "last_monitor_prob_is_fresh": "0",
        "last_monitor_market_price_is_fresh": "0",
    }
    runtime_select_expr = ", ".join(
        c if c in actual_cols else f"{default} AS {c}"
        for c, default in _runtime_cols_defaults.items()
    )

    rows = conn.execute(
        f"""
        SELECT position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
               direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
               last_monitor_prob, last_monitor_edge, last_monitor_market_price,
               decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
               chain_state, token_id, no_token_id, condition_id, order_id, order_status, updated_at,
               temperature_metric, {position_current_env_expr}, {authority_select_expr},
               {runtime_select_expr}
        FROM position_current {where_clause}
        ORDER BY updated_at DESC, position_id
        """,
        params,
    ).fetchall()
    if not rows:
        return {
            "status": "empty",
            "table": "position_current",
            "positions": [],
            "temperature_metric": temperature_metric,
        }

    trade_ids = [str(row["trade_id"] or row["position_id"] or "") for row in rows]
    position_ids = [str(row["position_id"] or row["trade_id"] or "") for row in rows]
    event_envs = _latest_position_event_envs(conn, position_ids)
    transitional_hints = _query_transitional_position_hints(conn, trade_ids)
    fill_hints = _query_entry_execution_fill_hints(conn, trade_ids)

    positions: list[dict] = []
    for row in rows:
        trade_id = str(row["trade_id"] or row["position_id"] or "")
        phase = str(row["phase"] or "")
        hints = transitional_hints.get(trade_id, {})
        exit_state_hint = str(hints.get("exit_state") or "")
        if phase != "pending_exit":
            exit_state_hint = ""
        fill_economics = _position_current_effective_entry_economics(
            row,
            fill_hints.get(trade_id),
        )
        runtime_state = _portfolio_loader_runtime_state_from_phase(phase)
        explicit_env = str(row["env"] or event_envs.get(str(row["position_id"] or "")) or "unknown_env")
        positions.append(
            {
                "trade_id": trade_id,
                "market_id": row["market_id"],
                "city": row["city"],
                "cluster": row["cluster"],
                "target_date": row["target_date"],
                "bin_label": row["bin_label"],
                "direction": row["direction"],
                "unit": row["unit"],
                "size_usd": fill_economics["effective_cost_basis_usd"],
                "submitted_size_usd": fill_economics["submitted_size_usd"],
                "shares": fill_economics["effective_shares"],
                "cost_basis_usd": fill_economics["pnl_cost_basis_usd"],
                "projection_cost_basis_usd": fill_economics["projection_cost_basis_usd"],
                "entry_price": fill_economics["effective_entry_price"],
                "entry_price_avg_fill": fill_economics["entry_price_avg_fill"],
                "shares_filled": fill_economics["shares_filled"],
                "filled_cost_basis_usd": fill_economics["filled_cost_basis_usd"],
                "effective_cost_basis_usd": fill_economics["effective_cost_basis_usd"],
                "entry_economics_authority": fill_economics["entry_economics_authority"],
                "fill_authority": fill_economics["fill_authority"],
                "entry_economics_source": fill_economics["entry_economics_source"],
                "execution_fact_intent_id": fill_economics["execution_fact_intent_id"],
                "execution_fact_filled_at": fill_economics["execution_fact_filled_at"],
                "p_posterior": row["p_posterior"],
                "entry_ci_width": row["entry_ci_width"],
                "exit_retry_count": row["exit_retry_count"],
                "next_exit_retry_at": row["next_exit_retry_at"],
                "last_monitor_prob": _finite_float_or_none(row["last_monitor_prob"]),
                "last_monitor_prob_is_fresh": bool(row["last_monitor_prob_is_fresh"] or False),
                "last_monitor_edge": _finite_float_or_none(row["last_monitor_edge"]),
                "last_monitor_market_price": row["last_monitor_market_price"],
                "last_monitor_market_price_is_fresh": bool(
                    row["last_monitor_market_price_is_fresh"] or False
                ),
                "decision_snapshot_id": str(row["decision_snapshot_id"] or ""),
                "entry_method": str(row["entry_method"] or ""),
                "strategy_key": str(row["strategy_key"] or ""),
                "strategy": str(row["strategy_key"] or ""),
                "edge_source": str(row["edge_source"] or ""),
                "discovery_mode": str(row["discovery_mode"] or ""),
                "chain_state": str(row["chain_state"] or "unknown"),
                "token_id": str(row["token_id"] or ""),
                "no_token_id": str(row["no_token_id"] or ""),
                "condition_id": str(row["condition_id"] or ""),
                "order_id": str(row["order_id"] or ""),
                "order_status": str(row["order_status"] or ""),
                "state": runtime_state,
                "env": explicit_env,
                "entered_at": str(hints.get("entered_at") or ""),
                "day0_entered_at": str(hints.get("day0_entered_at") or ""),
                "pre_exit_state": str(hints.get("pre_exit_state") or ""),
                "exit_state": exit_state_hint,
                "exit_reason": str(row["exit_reason"] or ""),
                "admin_exit_reason": str(row["admin_exit_reason"] or hints.get("admin_exit_reason") or ""),
                "entry_fill_verified": bool(
                    hints.get("entry_fill_verified", False)
                    or fill_economics["entry_fill_verified"]
                ),
                "temperature_metric": str(row["temperature_metric"] or "high"),
                # PR #352 (Part-5 audit Finding 1): durable chain-observation +
                # authority columns round-trip into the runtime Position so that
                # chain_verified_at / last_chain_absence_observed_at survive
                # restart. _position_from_projection_row maps chain_seen_at ->
                # chain_verified_at and chain_absence_at ->
                # last_chain_absence_observed_at. (fill_authority already flows
                # via fill_economics above.)
                "chain_seen_at": str(row["chain_seen_at"] or ""),
                "chain_absence_at": str(row["chain_absence_at"] or ""),
                "chain_shares": _finite_float_or_none(row["chain_shares"]) or 0.0,
                # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28):
                # chain-observed economics round-trip into Position via
                # _position_from_row (state/portfolio.py).
                "chain_avg_price": _finite_float_or_none(row["chain_avg_price"]) or 0.0,
                "chain_cost_basis_usd": _finite_float_or_none(row["chain_cost_basis_usd"]) or 0.0,
                "recovery_authority": str(row["recovery_authority"] or ""),
            }
        )
    return {
        "status": "ok" if positions else "empty",
        "table": "position_current",
        "positions": positions,
        "temperature_metric": temperature_metric,
    }


def upsert_control_override(
    conn: sqlite3.Connection | None,
    *,
    override_id: str,
    target_type: str,
    target_key: str,
    action_type: str,
    value: str,
    issued_by: str,
    issued_at: str,
    reason: str,
    effective_until: str | None = None,
    precedence: int = DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
) -> dict:
    """Append a control override event. Writes into the append-only
    `control_overrides_history` log; the `control_overrides` VIEW projects
    the latest row (by `history_id`, AUTOINCREMENT) per `override_id`. See B070."""
    if conn is None:
        return {"status": "skipped_no_connection", "table": "control_overrides"}
    if not _table_exists(conn, "control_overrides_history"):
        return {"status": "skipped_missing_table", "table": "control_overrides"}
    recorded_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO control_overrides_history (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, effective_until, reason, precedence,
            operation, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'upsert', ?)
        """,
        (
            override_id,
            target_type,
            target_key,
            action_type,
            value,
            issued_by,
            issued_at,
            effective_until,
            reason,
            precedence,
            recorded_at,
        ),
    )
    return {"status": "written", "table": "control_overrides", "override_id": override_id}


def record_token_suppression(
    conn: sqlite3.Connection | None,
    *,
    token_id: str,
    suppression_reason: str,
    source_module: str,
    condition_id: str | None = None,
    created_at: str | None = None,
    evidence: dict | None = None,
) -> dict:
    """Append a token suppression event to the append-only history log.

    B071: writes into `token_suppression_history` (append-only log) AND
    into the legacy `token_suppression` table (upsert, for backward compat
    with callers that query the legacy table directly). The `token_suppression_current`
    VIEW projects the latest row per `token_id` from the history table.

    After running migrate_b071_token_suppression_to_history.py --apply --drop-legacy,
    the legacy table is DROPped and replaced by a VIEW alias, and the dual-write
    can be removed in a future cleanup phase.
    """
    if conn is None:
        return {"status": "skipped_no_connection", "table": "token_suppression"}
    if not _table_exists(conn, "token_suppression_history"):
        return {"status": "skipped_missing_table", "table": "token_suppression"}
    normalized_token = str(token_id or "").strip()
    if not normalized_token:
        raise ValueError("token suppression requires token_id")
    normalized_reason = str(suppression_reason or "").strip()
    if normalized_reason not in TOKEN_SUPPRESSION_REASONS:
        raise ValueError(f"unknown token suppression reason: {suppression_reason!r}")
    normalized_source = str(source_module or "").strip()
    if not normalized_source:
        raise ValueError("token suppression requires source_module")
    now = created_at or datetime.now(timezone.utc).isoformat()
    recorded_at = datetime.now(timezone.utc).isoformat()
    evidence_payload = dict(evidence or {})
    if normalized_reason == "chain_only_quarantined":
        # Use MAX(history_id) — strictly monotone, no clock/tie dependency (B071).
        # Fall back to legacy token_suppression table if no history row exists yet
        # (pre-migration DBs that have rows only in the legacy table).
        existing = conn.execute(
            """
            SELECT suppression_reason, created_at, evidence_json
            FROM token_suppression_history
            WHERE token_id = ?
              AND history_id = (
                  SELECT MAX(h2.history_id)
                  FROM token_suppression_history h2
                  WHERE h2.token_id = ?
              )
            """,
            (normalized_token, normalized_token),
        ).fetchone()
        if existing is None and _table_exists(conn, "token_suppression"):
            existing = conn.execute(
                """
                SELECT suppression_reason, created_at, evidence_json
                FROM token_suppression
                WHERE token_id = ?
                """,
                (normalized_token,),
            ).fetchone()
        if existing is not None and str(existing["suppression_reason"] or "") == "chain_only_quarantined":
            try:
                existing_evidence = json.loads(str(existing["evidence_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                existing_evidence = {}
            first_seen_at = str(
                existing_evidence.get("first_seen_at")
                or existing["created_at"]
                or ""
            )
            if first_seen_at:
                evidence_payload["first_seen_at"] = first_seen_at
    evidence_json = json.dumps(evidence_payload, sort_keys=True)
    # B071 cycle-2 critic MINOR #1: wrap dual-write in a single transaction.
    # Without this, a failure between the history INSERT and the legacy UPSERT
    # leaves the two tables inconsistent — history says "suppressed" while
    # legacy still shows the prior state (or nothing). `with conn:` uses the
    # connection as a context manager that commits on success, rolls back on
    # exception. Dual-write becomes atomic at the write-side seam.
    with conn:
        # Append to history (B071 — append-only, audit trail).
        conn.execute(
            """
            INSERT INTO token_suppression_history (
                token_id, condition_id, suppression_reason, source_module,
                created_at, updated_at, evidence_json, operation, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'record', ?)
            """,
            (
                normalized_token,
                str(condition_id or ""),
                normalized_reason,
                normalized_source,
                now,
                now,
                evidence_json,
                recorded_at,
            ),
        )
        # Dual-write: keep legacy token_suppression table in sync for backward
        # compat with callers that query it directly (pre-migration). Removed
        # after migrate_b071 --drop-legacy creates the VIEW alias.
        if _table_exists(conn, "token_suppression"):
            conn.execute(
                """
                INSERT INTO token_suppression (
                    token_id, condition_id, suppression_reason, source_module,
                    created_at, updated_at, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_id) DO UPDATE SET
                    condition_id = CASE
                        WHEN excluded.condition_id IS NULL OR excluded.condition_id = ''
                        THEN token_suppression.condition_id
                        ELSE excluded.condition_id
                    END,
                    suppression_reason = excluded.suppression_reason,
                    source_module = excluded.source_module,
                    updated_at = excluded.updated_at,
                    evidence_json = excluded.evidence_json
                """,
                (
                    normalized_token,
                    str(condition_id or ""),
                    normalized_reason,
                    normalized_source,
                    now,
                    now,
                    evidence_json,
                ),
            )
    return {
        "status": "written",
        "table": "token_suppression",
        "token_id": normalized_token,
    }


def query_token_suppression_tokens(conn: sqlite3.Connection | None) -> list[str]:
    """Return tokens that reconciliation must not resurrect from chain-only state.

    Reads from `token_suppression` which is either the legacy mutable table
    (pre-migration) or the VIEW alias created by
    migrate_b071_token_suppression_to_history.py --apply --drop-legacy (B071).
    The VIEW projects the latest row per token_id from the append-only history.

    2026-05-27 fitz: ALSO include chain_only_quarantined tokens whose parent
    market has reached a chain-terminal phase (settled/voided/admin_closed/
    economically_closed/quarantined). Without this, reconcile_with_chain
    Rule 3 re-quarantines these tokens every cycle from the chain API
    response, regenerating chain-only quarantine positions in PortfolioState
    and re-arming _has_quarantined_positions even when the load-portfolio
    path correctly excludes them. The terminal-phase guard mirrors the one
    in query_chain_only_quarantine_rows so both injection paths agree.
    Skipped when position_current is absent (test envs).
    """
    if conn is None or not _table_or_view_exists(conn, "token_suppression"):
        return []
    base = conn.execute(
        f"""
        SELECT token_id
        FROM token_suppression
        WHERE suppression_reason IN ({", ".join(["?"] * len(RESOLVED_TOKEN_SUPPRESSION_REASONS))})
        ORDER BY created_at ASC, token_id ASC
        """,
        RESOLVED_TOKEN_SUPPRESSION_REASONS,
    ).fetchall()
    chain_terminal: list = []
    if _table_exists(conn, "position_current"):
        chain_terminal = conn.execute(
            """
            SELECT ts.token_id
            FROM token_suppression ts
            WHERE ts.suppression_reason = 'chain_only_quarantined'
              AND EXISTS (
                  SELECT 1 FROM position_current pc
                  WHERE (pc.token_id = ts.token_id OR pc.no_token_id = ts.token_id)
                    AND pc.phase IN ('settled', 'voided', 'admin_closed',
                                     'economically_closed', 'quarantined')
              )
            ORDER BY ts.created_at ASC, ts.token_id ASC
            """
        ).fetchall()
    chain_non_global: list = []
    if _table_or_view_exists(conn, "market_topology_state"):
        chain_rows = conn.execute(
            """
            SELECT token_id, condition_id
            FROM token_suppression
            WHERE suppression_reason = 'chain_only_quarantined'
            ORDER BY created_at ASC, token_id ASC
            """
        ).fetchall()
        chain_non_global = [
            row
            for row in chain_rows
            if chain_only_entry_block_scope(
                conn,
                condition_id=str(row["condition_id"] or ""),
            )
            != "global"
        ]
    out: list[str] = []
    seen: set[str] = set()
    for row in list(base) + list(chain_terminal) + list(chain_non_global):
        tok = str(row["token_id"] or "")
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def chain_only_entry_block_scope(
    conn: sqlite3.Connection | None,
    *,
    condition_id: str | None,
    decision_time: datetime | None = None,
) -> str:
    """Return the entry-block scope for a chain-only suppression fact.

    Chain-only tokens are a real review signal, but they are only a global
    entry kill-switch when they belong to the current Zeus weather topology.
    Non-weather inventory, missing topology, stale topology, and old weather
    dates remain position-level review debt so they cannot freeze all live
    trading.
    """

    if conn is None or not condition_id:
        return "position_only"
    if not _table_or_view_exists(conn, "market_topology_state"):
        return "global"
    try:
        row = conn.execute(
            """
            SELECT market_family, target_local_date, status, authority_status
            FROM market_topology_state
            WHERE condition_id = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            (str(condition_id),),
        ).fetchone()
    except sqlite3.Error:
        logger.warning(
            "chain-only scope lookup failed; preserving global entry block",
            exc_info=True,
        )
        return "global"
    if row is None:
        return "position_only"
    if str(row["market_family"] or "") != "weather_temperature":
        return "position_only"
    if str(row["status"] or "") != "CURRENT":
        return "position_only"
    if str(row["authority_status"] or "") != "VERIFIED":
        return "position_only"
    target_date_raw = str(row["target_local_date"] or "")
    try:
        target_date = datetime.strptime(target_date_raw, "%Y-%m-%d").date()
    except ValueError:
        return "position_only"
    now_dt = decision_time or datetime.now(timezone.utc)
    # Allow one UTC-day slack for west-of-UTC local trading days. Older weather
    # markets are settlement/redeem review debt, not current entry risk.
    if target_date < (now_dt.date() - timedelta(days=1)):
        return "position_only"
    return "global"


def _with_chain_only_entry_block_scope(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict:
    out = dict(row)
    out["entry_block_scope"] = chain_only_entry_block_scope(
        conn,
        condition_id=str(out.get("condition_id") or ""),
    )
    return out


def query_chain_only_quarantine_rows(conn: sqlite3.Connection | None) -> list[dict]:
    """Return unresolved chain-only quarantine facts for runtime cache hydration.

    Reads from `token_suppression` which is either the legacy mutable table
    (pre-migration) or the VIEW alias created by B071 migration. The VIEW
    projects the latest row per token_id from the append-only history.

    2026-05-27 fitz: exclude rows whose parent market has reached a runtime-
    inactive phase (settled / voided / admin_closed / economically_closed /
    quarantined). Per the chain-is-truth principle, once chain (or admin)
    finalizes a market, any chain-only-quarantined token on that market
    carries no live exposure and must not appear in the portfolio as a
    quarantined position blocking new entries. The suppression row remains
    in the append-only history for audit; this query filters it out of
    runtime consumption.

    Note the deliberate divergence from ``_CHAIN_TERMINAL_POSITION_PHASES``
    in ``src/execution/exchange_reconcile.py``, which is the stricter 3-phase
    chain-terminal set (settled / voided / admin_closed) used for drift
    suppression. The 5-phase set here matches INACTIVE_RUNTIME_STATES and is
    also used in ``query_token_suppression_tokens`` (Rule-3 ignored_tokens
    path).
    """
    if conn is None or not _table_or_view_exists(conn, "token_suppression"):
        return []
    # If position_current is absent we cannot determine terminal status —
    # fall back to original behavior (return all rows). Tests with minimal
    # schema land here.
    if not _table_exists(conn, "position_current"):
        rows = conn.execute(
            """
            SELECT token_id, condition_id, created_at, updated_at, evidence_json
            FROM token_suppression
            WHERE suppression_reason = 'chain_only_quarantined'
            ORDER BY created_at ASC, token_id ASC
            """
        ).fetchall()
        return [_with_chain_only_entry_block_scope(conn, row) for row in rows]
    rows = conn.execute(
        """
        SELECT ts.token_id, ts.condition_id, ts.created_at, ts.updated_at, ts.evidence_json
        FROM token_suppression ts
        WHERE ts.suppression_reason = 'chain_only_quarantined'
          AND NOT EXISTS (
              SELECT 1 FROM position_current pc
              WHERE (pc.token_id = ts.token_id OR pc.no_token_id = ts.token_id)
                AND pc.phase IN ('settled', 'voided', 'admin_closed', 'economically_closed', 'quarantined')
          )
        ORDER BY ts.created_at ASC, ts.token_id ASC
        """
    ).fetchall()
    return [_with_chain_only_entry_block_scope(conn, row) for row in rows]


def expire_control_override(
    conn: sqlite3.Connection | None,
    *,
    override_id: str,
    expired_at: str,
) -> dict:
    """Append an 'expire' event to `control_overrides_history` that sets
    `effective_until = expired_at` on the latest row for this override_id.
    No-op if no currently-active row exists. See B070."""
    if conn is None:
        return {"status": "skipped_no_connection", "table": "control_overrides", "expired_count": 0}
    if not _table_exists(conn, "control_overrides_history"):
        return {"status": "skipped_missing_table", "table": "control_overrides", "expired_count": 0}
    recorded_at = datetime.now(timezone.utc).isoformat()
    # Use history_id (AUTOINCREMENT) not recorded_at for the latest-row
    # lookup: strictly monotone, no clock/tie dependency.
    cur = conn.execute(
        """
        INSERT INTO control_overrides_history (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, effective_until, reason, precedence,
            operation, recorded_at
        )
        SELECT h.override_id, h.target_type, h.target_key, h.action_type, h.value,
               h.issued_by, h.issued_at, ?, h.reason, h.precedence,
               'expire', ?
        FROM control_overrides_history h
        WHERE h.override_id = ?
          AND h.history_id = (
              SELECT MAX(h2.history_id)
              FROM control_overrides_history h2
              WHERE h2.override_id = ?
          )
          AND (h.effective_until IS NULL OR h.effective_until > ?)
        """,
        (expired_at, recorded_at, override_id, override_id, expired_at),
    )
    return {
        "status": "expired" if cur.rowcount else "noop",
        "table": "control_overrides",
        "expired_count": int(cur.rowcount or 0),
        "override_id": override_id,
    }


def query_control_override_state(
    conn: sqlite3.Connection | None,
    *,
    now: str | None = None,
) -> dict:
    current_time = now or datetime.now(timezone.utc).isoformat()
    if conn is None:
        return {
            "status": "skipped_no_connection",
            "entries_paused": False,
            "entries_pause_source": None,
            "entries_pause_reason": None,
            "edge_threshold_multiplier": 1.0,
            "strategy_gates": {},
        }
    control_overrides_ref = _control_overrides_authority_ref(conn)
    risk_actions_ref = _strategy_risk_actions_authority_ref(conn)
    if control_overrides_ref is None and risk_actions_ref is None:
        return {
            "status": "missing_table",
            "entries_paused": False,
            "entries_pause_source": None,
            "entries_pause_reason": None,
            "edge_threshold_multiplier": 1.0,
            "strategy_gates": {},
        }
    if control_overrides_ref is None:
        rows = []
    else:
        rows = conn.execute(
            f"""
            SELECT override_id, target_type, target_key, action_type, value, issued_by,
                   issued_at, effective_until, reason, precedence
            FROM {control_overrides_ref}
            WHERE target_type IN ('global', 'strategy')
              AND issued_at <= ?
              AND (effective_until IS NULL OR effective_until > ?)
            ORDER BY precedence DESC, issued_at DESC, override_id DESC
            """,
            (current_time, current_time),
        ).fetchall()
    entries_paused = False
    entries_pause_source = None
    entries_pause_reason = None
    entries_pause_issued_at = None
    entries_pause_effective_until = None
    entries_pause_issued_by = None
    edge_threshold_multiplier = 1.0
    # G6 BLOCKER #2 fix (2026-04-26, con-nyx review): emit GateDecision-shaped
    # dicts (not bare bool) so control_plane.strategy_gates() — which expects
    # dict and raises ValueError on bool — can deserialize them via
    # GateDecision.from_dict. K1 migration set the in-memory writer
    # (set_strategy_gate puts dict) but missed the DB reader; the boot
    # guard introduced by G6 forced this latent debt onto every live launch.
    strategy_gates: dict[str, dict] = {}
    seen_strategy_gate: set[str] = set()
    global_gate_seen = False
    global_threshold_seen = False
    for row in rows:
        target_type = str(row["target_type"] or "")
        target_key = str(row["target_key"] or "")
        action_type = str(row["action_type"] or "")
        value = str(row["value"] or "")
        if target_type == "global" and target_key == "entries" and action_type == "gate" and not global_gate_seen:
            entries_paused = _parse_boolish_text(value)
            if entries_paused:
                reason = str(row["reason"] or "")
                issued_by = str(row["issued_by"] or "")
                entries_pause_issued_at = str(row["issued_at"] or "")
                entries_pause_effective_until = row["effective_until"]
                entries_pause_issued_by = issued_by
                if issued_by == "system_auto_pause" or issued_by.startswith("auto:"):
                    entries_pause_source = "auto_exception"
                    entries_pause_reason = reason if issued_by == "system_auto_pause" else issued_by.replace("auto:", "", 1)
                elif issued_by == "control_plane":
                    entries_pause_source = "manual_command"
                    entries_pause_reason = reason
                else:
                    entries_pause_source = "manual_command"
                    entries_pause_reason = f"external:{issued_by}"
            global_gate_seen = True
            continue
        if target_type == "global" and target_key == "entries" and action_type == "threshold_multiplier" and not global_threshold_seen:
            try:
                edge_threshold_multiplier = max(1.0, float(value))
            except (TypeError, ValueError):
                edge_threshold_multiplier = 1.0
            global_threshold_seen = True
            continue
        if target_type == "strategy" and action_type == "gate" and target_key and target_key not in seen_strategy_gate:
            # value="true" means gate IS active (strategy DISABLED), so enabled = NOT value.
            # Synthesize GateDecision-shape from the row columns the DB already carries.
            # reason_code defaults to OPERATOR_OVERRIDE since the DB doesn't store the original
            # ReasonCode enum; reason_snapshot empty (DB doesn't store snapshot either).
            strategy_gates[target_key] = {
                "enabled": not _parse_boolish_text(value),
                "reason_code": "operator_override",
                "reason_snapshot": {},
                "gated_at": str(row["issued_at"] or ""),
                "gated_by": str(row["issued_by"] or "unknown"),
            }
            seen_strategy_gate.add(target_key)
    if risk_actions_ref is not None:
        action_rows = conn.execute(
            f"""
            SELECT action_id, strategy_key, action_type, value, issued_at, effective_until,
                   reason, source, precedence, status
            FROM {risk_actions_ref}
            WHERE action_type = 'gate'
              AND status = 'active'
              AND issued_at <= ?
              AND (effective_until IS NULL OR effective_until > ?)
            ORDER BY precedence DESC, issued_at DESC, action_id DESC
            """,
            (current_time, current_time),
        ).fetchall()
        for row in action_rows:
            target_key = str(row["strategy_key"] or "")
            if not target_key or target_key in seen_strategy_gate:
                continue
            strategy_gates[target_key] = {
                "enabled": not _parse_boolish_text(str(row["value"] or "")),
                "reason_code": "riskguard_action",
                "reason_snapshot": {
                    "action_id": str(row["action_id"] or ""),
                    "reason": str(row["reason"] or ""),
                    "source": str(row["source"] or ""),
                    "precedence": int(row["precedence"] or 0),
                },
                "gated_at": str(row["issued_at"] or ""),
                "gated_by": f"auto:{str(row['source'] or 'risk_action')}",
            }
            seen_strategy_gate.add(target_key)
    return {
        "status": "ok",
        "entries_paused": entries_paused,
        "entries_pause_source": entries_pause_source,
        "entries_pause_reason": entries_pause_reason,
        "entries_pause_issued_at": entries_pause_issued_at,
        "entries_pause_effective_until": entries_pause_effective_until,
        "entries_pause_issued_by": entries_pause_issued_by,
        "edge_threshold_multiplier": edge_threshold_multiplier,
        "strategy_gates": strategy_gates,
    }


def _database_files_by_schema(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, str] = {}
    for row in rows:
        try:
            schema = str(row["name"] or "")
            file_name = str(row["file"] or "")
        except (KeyError, IndexError, TypeError):
            schema = str(row[1] or "")
            file_name = str(row[2] or "")
        if schema:
            out[schema] = file_name
    return out


def _schema_has_object(conn: sqlite3.Connection, schema: str, name: str) -> bool:
    if not schema.replace("_", "").isalnum():
        return False
    try:
        row = conn.execute(
            f"""
            SELECT 1
            FROM {schema}.sqlite_master
            WHERE name = ?
              AND type IN ('table', 'view')
            LIMIT 1
            """,
            (name,),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _control_overrides_authority_ref(conn: sqlite3.Connection) -> str | None:
    databases = _database_files_by_schema(conn)
    if _schema_has_object(conn, "world", "control_overrides"):
        return "world.control_overrides"
    main_file = Path(databases.get("main", "")).name
    if main_file == "zeus_trades.db":
        return None
    if _schema_has_object(conn, "main", "control_overrides"):
        return "control_overrides"
    return None


def _strategy_risk_actions_authority_ref(conn: sqlite3.Connection) -> str | None:
    databases = _database_files_by_schema(conn)
    if _schema_has_object(conn, "trades", "risk_actions"):
        return "trades.risk_actions"
    main_file = Path(databases.get("main", "")).name
    if main_file == "zeus-world.db":
        return None
    if _schema_has_object(conn, "main", "risk_actions"):
        return "risk_actions"
    return None


def _shift_iso_timestamp(timestamp: str, *, days: int) -> str:
    parsed = _parse_iso_timestamp(timestamp)
    if parsed is None:
        return timestamp
    return (parsed - timedelta(days=days)).isoformat()


def _parse_boolish_text(raw: str) -> bool:
    # K1/#71: removed "gate" — action keyword, not boolean literal.
    # Same rationale as _parse_boolish in policy.py.
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError(f"unsupported boolish value in DB: {raw!r}")


def _finite_float_or_zero(value) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return 0.0
    return numeric


def _finite_float_or_none(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _query_entry_execution_fill_hints(
    conn: sqlite3.Connection,
    trade_ids: list[str],
) -> dict[str, dict]:
    """Return confirmed entry fill economics from canonical execution facts.

    `position_current` lacks durable fill-authority columns. This read-side
    enrichment is intentionally narrower than a schema migration: it consumes
    only terminal filled entry execution facts with filled_at + positive price
    and shares, then leaves legacy/projection rows explicitly non-fill-grade.
    """
    if not trade_ids or not _table_exists(conn, "execution_fact"):
        return {}
    columns = _table_columns(conn, "execution_fact")
    required = {
        "intent_id",
        "position_id",
        "order_role",
        "filled_at",
        "posted_at",
        "fill_price",
        "shares",
        "terminal_exec_status",
        "venue_status",
    }
    if not required.issubset(columns):
        return {}
    normalized_trade_ids = sorted({str(trade_id or "") for trade_id in trade_ids if str(trade_id or "")})
    if not normalized_trade_ids:
        return {}
    placeholders = ", ".join("?" for _ in normalized_trade_ids)
    rows = conn.execute(
        f"""
        SELECT position_id, intent_id, filled_at, posted_at, fill_price, shares,
               terminal_exec_status, venue_status
        FROM execution_fact
        WHERE position_id IN ({placeholders})
          AND order_role = 'entry'
          AND lower(COALESCE(terminal_exec_status, '')) = 'filled'
          AND filled_at IS NOT NULL
          AND COALESCE(fill_price, 0.0) > 0.0
          AND COALESCE(shares, 0.0) > 0.0
        ORDER BY position_id,
                 COALESCE(filled_at, posted_at, '') DESC,
                 intent_id DESC
        """,
        normalized_trade_ids,
    ).fetchall()
    hints: dict[str, dict] = {}
    for row in rows:
        trade_id = str(row["position_id"] or "")
        if not trade_id or trade_id in hints:
            continue
        fill_price = _finite_float_or_zero(row["fill_price"])
        shares = _finite_float_or_zero(row["shares"])
        filled_cost_basis_usd = fill_price * shares
        if fill_price <= 0.0 or shares <= 0.0 or filled_cost_basis_usd <= 0.0:
            continue
        hints[trade_id] = {
            "entry_price_avg_fill": fill_price,
            "shares_filled": shares,
            "filled_cost_basis_usd": filled_cost_basis_usd,
            "entry_economics_authority": ENTRY_ECONOMICS_AVG_FILL_PRICE,
            "fill_authority": FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
            "entry_fill_verified": True,
            "entry_economics_source": "execution_fact",
            "execution_fact_intent_id": str(row["intent_id"] or ""),
            "execution_fact_filled_at": str(row["filled_at"] or ""),
            "execution_fact_venue_status": str(row["venue_status"] or ""),
        }
    return hints


def _position_current_effective_entry_economics(row, fill_hint: dict | None) -> dict:
    from src.state.portfolio import (
        fill_authority_effective_open_cost_basis,
        has_verified_trade_fill,
    )

    def _row_optional(key: str, default: object = None) -> object:
        try:
            return row[key]
        except (IndexError, KeyError):
            return default

    submitted_size_usd = _finite_float_or_zero(row["size_usd"])
    projection_shares = _finite_float_or_zero(row["shares"])
    projection_cost_basis_usd = _finite_float_or_zero(row["cost_basis_usd"])
    projection_entry_price = _finite_float_or_zero(row["entry_price"])
    chain_shares = _finite_float_or_zero(_row_optional("chain_shares"))
    chain_cost_basis_usd = _finite_float_or_zero(_row_optional("chain_cost_basis_usd"))
    chain_avg_price = _finite_float_or_zero(_row_optional("chain_avg_price"))
    phase = str(row["phase"] or "")
    row_fill_authority = str(_row_optional("fill_authority") or "").strip()

    if fill_hint:
        filled_cost_basis_usd = _finite_float_or_zero(fill_hint.get("filled_cost_basis_usd"))
        filled_shares = _finite_float_or_zero(fill_hint.get("shares_filled"))
        avg_fill_price = _finite_float_or_zero(fill_hint.get("entry_price_avg_fill"))
        if (
            projection_shares > filled_shares + 1e-9
            and projection_cost_basis_usd > filled_cost_basis_usd + 1e-9
            and chain_shares >= projection_shares - 1e-9
            and chain_cost_basis_usd >= projection_cost_basis_usd - 1e-9
        ):
            effective_entry_price = chain_avg_price or projection_entry_price
            if effective_entry_price <= 0.0 and projection_cost_basis_usd > 0.0 and projection_shares > 0.0:
                effective_entry_price = projection_cost_basis_usd / projection_shares
            return {
                "submitted_size_usd": submitted_size_usd,
                "projection_cost_basis_usd": projection_cost_basis_usd,
                "effective_cost_basis_usd": projection_cost_basis_usd,
                "effective_shares": projection_shares,
                "pnl_cost_basis_usd": projection_cost_basis_usd,
                "effective_entry_price": effective_entry_price,
                "entry_price_avg_fill": effective_entry_price,
                "shares_filled": projection_shares,
                "filled_cost_basis_usd": projection_cost_basis_usd,
                "entry_economics_authority": ENTRY_ECONOMICS_CORRECTED_COST_BASIS,
                "fill_authority": (
                    row_fill_authority
                    if row_fill_authority and row_fill_authority != FILL_AUTHORITY_NONE
                    else FILL_AUTHORITY_VENUE_POSITION_OBSERVED
                ),
                "entry_economics_source": "position_current_chain_corrected",
                "entry_fill_verified": True,
                "execution_fact_intent_id": str(fill_hint.get("execution_fact_intent_id") or ""),
                "execution_fact_filled_at": str(fill_hint.get("execution_fact_filled_at") or ""),
                "execution_fact_venue_status": str(fill_hint.get("execution_fact_venue_status") or ""),
            }
        effective_cost_basis_usd = fill_authority_effective_open_cost_basis(
            current_open_cost=projection_cost_basis_usd,
            current_open_shares=projection_shares,
            entry_fill_cost=filled_cost_basis_usd,
            entry_fill_shares=filled_shares,
        )
        effective_shares = filled_shares
        if projection_shares > 0.0:
            effective_shares = min(projection_shares, filled_shares)
        effective_entry_price = avg_fill_price
        if effective_entry_price <= 0.0 and effective_cost_basis_usd > 0.0 and effective_shares > 0.0:
            effective_entry_price = effective_cost_basis_usd / effective_shares
        return {
            "submitted_size_usd": submitted_size_usd,
            "projection_cost_basis_usd": projection_cost_basis_usd,
            "effective_cost_basis_usd": effective_cost_basis_usd,
            "effective_shares": effective_shares,
            "pnl_cost_basis_usd": effective_cost_basis_usd,
            "effective_entry_price": effective_entry_price,
            "entry_price_avg_fill": avg_fill_price,
            "shares_filled": filled_shares,
            "filled_cost_basis_usd": filled_cost_basis_usd,
            "entry_economics_authority": ENTRY_ECONOMICS_AVG_FILL_PRICE,
            "fill_authority": FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
            "entry_economics_source": str(fill_hint.get("entry_economics_source") or "execution_fact"),
            "entry_fill_verified": True,
            "execution_fact_intent_id": str(fill_hint.get("execution_fact_intent_id") or ""),
            "execution_fact_filled_at": str(fill_hint.get("execution_fact_filled_at") or ""),
            "execution_fact_venue_status": str(fill_hint.get("execution_fact_venue_status") or ""),
        }

    if (
        chain_shares > 0.0
        and chain_cost_basis_usd > 0.0
        and row_fill_authority
        and row_fill_authority != FILL_AUTHORITY_NONE
    ):
        effective_entry_price = chain_avg_price
        if effective_entry_price <= 0.0:
            effective_entry_price = chain_cost_basis_usd / chain_shares
        return {
            "submitted_size_usd": submitted_size_usd,
            "projection_cost_basis_usd": projection_cost_basis_usd,
            "effective_cost_basis_usd": chain_cost_basis_usd,
            "effective_shares": chain_shares,
            "pnl_cost_basis_usd": chain_cost_basis_usd,
            "effective_entry_price": effective_entry_price,
            "entry_price_avg_fill": effective_entry_price,
            "shares_filled": chain_shares,
            "filled_cost_basis_usd": chain_cost_basis_usd,
            "entry_economics_authority": ENTRY_ECONOMICS_CORRECTED_COST_BASIS,
            "fill_authority": row_fill_authority,
            "entry_economics_source": "position_current_chain_observed",
            "entry_fill_verified": has_verified_trade_fill({"fill_authority": row_fill_authority}),
            "execution_fact_intent_id": "",
            "execution_fact_filled_at": "",
            "execution_fact_venue_status": "",
        }

    if phase == "pending_entry":
        return {
            "submitted_size_usd": submitted_size_usd,
            "projection_cost_basis_usd": projection_cost_basis_usd,
            "effective_cost_basis_usd": 0.0,
            "effective_shares": 0.0,
            "pnl_cost_basis_usd": 0.0,
            "effective_entry_price": 0.0,
            "entry_price_avg_fill": 0.0,
            "shares_filled": 0.0,
            "filled_cost_basis_usd": 0.0,
            "entry_economics_authority": ENTRY_ECONOMICS_LEGACY_UNKNOWN,
            "fill_authority": FILL_AUTHORITY_NONE,
            "entry_economics_source": "pending_entry_without_fill_authority",
            "entry_fill_verified": False,
            "execution_fact_intent_id": "",
            "execution_fact_filled_at": "",
            "execution_fact_venue_status": "",
        }

    pnl_cost_basis_usd = projection_cost_basis_usd if projection_cost_basis_usd > 0.0 else submitted_size_usd
    # PR #355 Copilot SEV-1: if the projection row already carries a non-NULL,
    # non-"none" fill_authority (e.g. "venue_position_observed" written by the
    # F1 balance-only rescue), honour it rather than unconditionally returning
    # FILL_AUTHORITY_NONE.  The Position properties (effective_shares,
    # effective_cost_basis_usd, effective_exposure) already route correctly via
    # has_chain_observed_authority when fill_authority is preserved here.
    effective_fill_authority = (
        row_fill_authority
        if row_fill_authority and row_fill_authority != FILL_AUTHORITY_NONE
        else FILL_AUTHORITY_NONE
    )
    return {
        "submitted_size_usd": submitted_size_usd,
        "projection_cost_basis_usd": projection_cost_basis_usd,
        "effective_cost_basis_usd": submitted_size_usd,
        "effective_shares": projection_shares,
        "pnl_cost_basis_usd": pnl_cost_basis_usd,
        "effective_entry_price": projection_entry_price,
        "entry_price_avg_fill": 0.0,
        "shares_filled": 0.0,
        "filled_cost_basis_usd": 0.0,
        "entry_economics_authority": ENTRY_ECONOMICS_LEGACY_UNKNOWN,
        "fill_authority": effective_fill_authority,
        "entry_economics_source": "position_current_projection",
        "entry_fill_verified": False,
        "execution_fact_intent_id": "",
        "execution_fact_filled_at": "",
        "execution_fact_venue_status": "",
    }






def _query_transitional_position_hints(
    conn: sqlite3.Connection,
    trade_ids: list[str],
) -> dict[str, dict]:
    if not trade_ids:
        return {}
    columns = _table_columns(conn, "position_events")
    placeholders = ", ".join("?" for _ in trade_ids)
    if {"position_id", "payload_json", "occurred_at"}.issubset(columns):
        event_placeholders = ", ".join("?" for _ in _TRANSITIONAL_HINT_EVENT_TYPES)
        payload_predicate = " OR ".join("payload_json LIKE ?" for _ in _TRANSITIONAL_HINT_PAYLOAD_KEYS)
        params = (
            *trade_ids,
            *_TRANSITIONAL_HINT_EVENT_TYPES,
            *(f"%{key}%" for key in _TRANSITIONAL_HINT_PAYLOAD_KEYS),
            _TRANSITIONAL_HINT_ROWS_PER_POSITION,
        )
        rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT position_id AS trade_key,
                       event_type,
                       payload_json AS payload,
                       occurred_at,
                       sequence_no,
                       ROW_NUMBER() OVER (
                           PARTITION BY position_id
                           ORDER BY occurred_at DESC, sequence_no DESC
                       ) AS rn
                FROM position_events
                WHERE position_id IN ({placeholders})
                  AND (
                      event_type IN ({event_placeholders})
                      OR {payload_predicate}
                  )
            )
            SELECT trade_key, event_type, payload, occurred_at
            FROM ranked
            WHERE rn <= ?
            ORDER BY occurred_at DESC, sequence_no DESC
            """,
            params,
        ).fetchall()
    else:
        logger.warning("position_events table missing expected columns"); return {}
    hints: dict[str, dict] = {}
    for row in rows:
        trade_id = str(row["trade_key"] or "")
        if not trade_id:
            continue
        bucket = hints.setdefault(trade_id, {})
        try:
            details = json.loads(row["payload"] or "{}")
        except Exception:
            details = {}
        occurred_at = str(row["occurred_at"] or "")
        if "entry_fill_verified" not in bucket and "entry_fill_verified" in details:
            bucket["entry_fill_verified"] = bool(details.get("entry_fill_verified"))
        if "admin_exit_reason" not in bucket and details.get("admin_exit_reason"):
            bucket["admin_exit_reason"] = str(details.get("admin_exit_reason"))
        if "day0_entered_at" not in bucket and details.get("day0_entered_at"):
            bucket["day0_entered_at"] = str(details.get("day0_entered_at"))
        elif (
            "day0_entered_at" not in bucket
            and row["event_type"] == "DAY0_WINDOW_ENTERED"
            and occurred_at
        ):
            bucket["day0_entered_at"] = occurred_at
        if (
            "order_posted_at" not in bucket
            and row["event_type"] in {"POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED"}
            and occurred_at
        ):
            bucket["order_posted_at"] = occurred_at
        if (
            "entered_at" not in bucket
            and row["event_type"] == "ENTRY_ORDER_FILLED"
            and occurred_at
        ):
            bucket["entered_at"] = occurred_at
        if "exit_state" not in bucket:
            if row["event_type"] == "EXIT_RETRY_RELEASED":
                bucket["exit_state"] = ""
                continue
            exit_state = _exit_state_hint_from_event(str(row["event_type"] or ""), details)
            if exit_state:
                bucket["exit_state"] = exit_state
        # Non-settlement lifecycle hints are env-filtered by their caller scope.
    _hydrate_unbounded_day0_hints(conn, trade_ids, hints)
    _hydrate_pending_exit_pre_state_hints(conn, trade_ids, hints)
    return hints


def _hydrate_unbounded_day0_hints(
    conn: sqlite3.Connection,
    trade_ids: list[str],
    hints: dict[str, dict],
) -> None:
    """Hydrate latest Day0 identity independently of the generic recent-row cap.

    A restarted monitor must know that an old pending-exit position already
    entered Day0 before it decides where retry cooldown should release. Day0
    identity is durable lifecycle state, not a best-effort cache hint, so read
    its latest event directly instead of relying only on the generic hint scan.
    """

    missing = [trade_id for trade_id in trade_ids if not hints.get(trade_id, {}).get("day0_entered_at")]
    if not missing:
        return
    placeholders = ", ".join("?" for _ in missing)
    try:
        rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT position_id,
                       payload_json,
                       occurred_at,
                       sequence_no,
                       ROW_NUMBER() OVER (
                           PARTITION BY position_id
                           ORDER BY occurred_at DESC, sequence_no DESC
                       ) AS rn
                  FROM position_events
                 WHERE position_id IN ({placeholders})
                   AND event_type = 'DAY0_WINDOW_ENTERED'
            )
            SELECT position_id, payload_json, occurred_at
              FROM ranked
             WHERE rn = 1
            """,
            tuple(missing),
        ).fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        trade_id = str(row["position_id"] or "")
        if not trade_id:
            continue
        try:
            details = json.loads(row["payload_json"] or "{}")
        except Exception:
            details = {}
        day0_entered_at = str(details.get("day0_entered_at") or row["occurred_at"] or "")
        if day0_entered_at:
            hints.setdefault(trade_id, {})["day0_entered_at"] = day0_entered_at


def _hydrate_pending_exit_pre_state_hints(
    conn: sqlite3.Connection,
    trade_ids: list[str],
    hints: dict[str, dict],
) -> None:
    """Hydrate the phase a pending-exit retry should release back to.

    ``pre_exit_state`` is runtime-only on Position, but the canonical event
    spine records it as ``phase_before`` for exit-intent/reject events. Loader
    recovery must not be polluted by later no-op ``pending_exit -> pending_exit``
    events such as chain corrections.
    """

    missing = [trade_id for trade_id in trade_ids if not hints.get(trade_id, {}).get("pre_exit_state")]
    if not missing:
        return
    placeholders = ", ".join("?" for _ in missing)
    try:
        rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT position_id,
                       phase_before,
                       phase_after,
                       occurred_at,
                       sequence_no,
                       ROW_NUMBER() OVER (
                           PARTITION BY position_id
                           ORDER BY occurred_at DESC, sequence_no DESC
                       ) AS rn
                 FROM position_events
                 WHERE position_id IN ({placeholders})
                   AND phase_after = 'pending_exit'
                   AND COALESCE(phase_before, '') != ''
                   AND phase_before != phase_after
            )
            SELECT position_id, phase_before
              FROM ranked
             WHERE rn = 1
            """,
            tuple(missing),
        ).fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        trade_id = str(row["position_id"] or "")
        phase_before = str(row["phase_before"] or "")
        if trade_id and phase_before:
            hints.setdefault(trade_id, {})["pre_exit_state"] = (
                _portfolio_loader_runtime_state_from_phase(phase_before)
            )


def _exit_state_hint_from_event(event_type: str, details: dict) -> str | None:
    if event_type not in _EXIT_LIFECYCLE_EVENT_TYPES:
        return None
    raw = details.get("exit_state")
    if raw in (None, ""):
        raw = details.get("status")
    exit_state = str(raw or "").strip()
    if exit_state in _EXIT_STATE_HINT_VALUES:
        return exit_state
    return None


def _settlement_authority_smoke_summary(conn: sqlite3.Connection) -> dict:
    original_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        rows = query_authoritative_settlement_rows(conn, limit=None)
    finally:
        conn.row_factory = original_row_factory
    ready_rows = 0
    learning_rows = 0
    degraded_rows = 0
    authority_levels: dict[str, int] = {}
    for row in rows:
        level = str(row.get("authority_level") or "unknown")
        authority_levels[level] = authority_levels.get(level, 0) + 1
        if row.get("is_degraded", False):
            degraded_rows += 1
        if row.get("metric_ready", False) and not row.get("is_degraded", False):
            ready_rows += 1
        if row.get("learning_snapshot_ready", False) and not row.get("is_degraded", False):
            learning_rows += 1

    surface_available = (
        (_table_exists(conn, "position_events") and _table_exists(conn, "position_current"))
        or _table_exists(conn, "decision_log")
    )
    return {
        "source": SETTLEMENT_AUTHORITY_DIAGNOSTIC_SOURCE,
        "surface_available": surface_available,
        "ready_rows": ready_rows,
        "learning_eligible_rows": learning_rows,
        "degraded_rows": degraded_rows,
        "authority_levels": authority_levels,
    }


def query_p4_fact_smoke_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    missing_tables = [
        table
        for table in ("opportunity_fact", "availability_fact", "execution_fact", "outcome_fact")
        if not _table_exists(conn, table)
    ]
    summary: dict[str, Any] = {
        "missing_tables": missing_tables,
        "opportunity": {"total": 0, "trade_eligible": 0, "no_trade": 0, "availability_tagged": 0},
        "availability": {"total": 0, "failure_types": {}},
        "execution": {
            "total": 0,
            "terminal_status_counts": {},
            "avg_fill_quality": None,
            "authority_scope": EXECUTION_FACT_AUTHORITY_SCOPE,
        },
        "outcome": {
            "total": 0,
            "wins": 0,
            "pnl_total": 0.0,
            "authority_scope": LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE,
            "learning_eligible": False,
            "promotion_eligible": False,
        },
        "settlement_authority": _settlement_authority_smoke_summary(conn),
        "separation": {
            "opportunity_loss_without_availability": 0,
            "availability_failures": 0,
            "execution_vs_outcome_gap": 0,
        },
    }

    if "opportunity_fact" not in missing_tables:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN should_trade = 1 THEN 1 ELSE 0 END) AS trade_eligible,
                SUM(CASE WHEN should_trade = 0 THEN 1 ELSE 0 END) AS no_trade,
                SUM(CASE WHEN availability_status IS NOT NULL AND availability_status != 'ok' THEN 1 ELSE 0 END) AS availability_tagged,
                SUM(CASE WHEN should_trade = 0 AND (availability_status IS NULL OR availability_status = 'ok') THEN 1 ELSE 0 END) AS no_availability_loss
            FROM opportunity_fact
            """
        ).fetchone()
        summary["opportunity"] = {
            "total": int(row["total"] or 0),
            "trade_eligible": int(row["trade_eligible"] or 0),
            "no_trade": int(row["no_trade"] or 0),
            "availability_tagged": int(row["availability_tagged"] or 0),
        }
        summary["separation"]["opportunity_loss_without_availability"] = int(row["no_availability_loss"] or 0)

    if "availability_fact" not in missing_tables:
        rows = conn.execute(
            "SELECT failure_type, COUNT(*) AS n FROM availability_fact GROUP BY failure_type"
        ).fetchall()
        failure_types = {str(r["failure_type"]): int(r["n"]) for r in rows}
        summary["availability"] = {
            "total": sum(failure_types.values()),
            "failure_types": failure_types,
        }
        summary["separation"]["availability_failures"] = summary["availability"]["total"]

    if "execution_fact" not in missing_tables:
        rows = conn.execute(
            "SELECT terminal_exec_status, COUNT(*) AS n FROM execution_fact GROUP BY terminal_exec_status"
        ).fetchall()
        status_counts = {str(r["terminal_exec_status"] or ""): int(r["n"]) for r in rows}
        row = conn.execute(
            """
            SELECT COUNT(*) AS total, AVG(fill_quality) AS avg_fill_quality
            FROM execution_fact
            """
        ).fetchone()
        summary["execution"] = {
            "total": int(row["total"] or 0),
            "terminal_status_counts": status_counts,
            "avg_fill_quality": float(row["avg_fill_quality"]) if row["avg_fill_quality"] is not None else None,
            "authority_scope": EXECUTION_FACT_AUTHORITY_SCOPE,
        }

    if "outcome_fact" not in missing_tables:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(COALESCE(pnl, 0.0)) AS pnl_total
            FROM outcome_fact
            """
        ).fetchone()
        summary["outcome"] = {
            "total": int(row["total"] or 0),
            "wins": int(row["wins"] or 0),
            "pnl_total": float(row["pnl_total"] or 0.0),
            "authority_scope": LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE,
            "learning_eligible": False,
            "promotion_eligible": False,
        }
    summary["separation"]["execution_vs_outcome_gap"] = max(
        0,
        summary["execution"]["total"] - summary["outcome"]["total"],
    )
    return summary


def query_execution_event_summary(
    conn: sqlite3.Connection,
    *,
    limit: int | None = 500,
    not_before: str | None = None,
) -> dict[str, Any]:
    """Execution event summary from canonical position_events."""
    filters = []
    params: list[object] = []
    if not_before is not None:
        filters.append("occurred_at >= ?")
        params.append(not_before)
    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""
    query = f"""
        SELECT event_type, strategy_key
        FROM position_events
        {where_clause}
        ORDER BY occurred_at DESC
        """
    if limit is not None:
        query += "\n        LIMIT ?"
        params.append(limit)
    try:
        rows = conn.execute(query, params).fetchall()
    except Exception:
        rows = []

    def _blank() -> dict[str, int]:
        return {
            "entry_attempted": 0,
            "entry_filled": 0,
            "entry_rejected": 0,
            "exit_attempted": 0,
            "exit_filled": 0,
            "exit_retry_scheduled": 0,
            "exit_backoff_exhausted": 0,
            "exit_fill_check_failed": 0,
            "exit_fill_checked": 0,
            "exit_fill_confirmed": 0,
            "exit_retry_released": 0,
        }

    overall = _blank()
    by_strategy: dict[str, dict[str, int]] = {}

    mapping = {
        "POSITION_OPEN_INTENT": "entry_attempted",
        "ENTRY_ORDER_FILLED": "entry_filled",
        "ENTRY_ORDER_REJECTED": "entry_rejected",
        "EXIT_ORDER_POSTED": "exit_attempted",
        "EXIT_ORDER_FILLED": "exit_filled",
        "EXIT_ORDER_VOIDED": "exit_fill_confirmed",
        "EXIT_ORDER_REJECTED": "exit_backoff_exhausted",
        "EXIT_RETRY_SCHEDULED": "exit_retry_scheduled",
    }

    for row in rows:
        event_type = str(row["event_type"])
        counter_key = mapping.get(event_type)
        if counter_key is None:
            continue
        overall[counter_key] += 1
        strategy = str(row["strategy_key"] or "unclassified")
        bucket = by_strategy.setdefault(strategy, _blank())
        bucket[counter_key] += 1

    return {
        "event_sample_size": len(rows),
        "overall": overall,
        "by_strategy": by_strategy,
    }


# ---------------------------------------------------------------------------
# transition_phase — single writer for pending_exit phase mutations
# ---------------------------------------------------------------------------
#
# Implementation moved to src/state/canonical_write.py (WAVE-3 Batch B bot
# review fix, 2026-05-18) to keep db.py (K0) free of K2 engine imports.
# This re-export preserves all existing import sites: callers that do
#   from src.state.db import transition_phase
# continue to work without modification.
def transition_phase(
    conn: "sqlite3.Connection | None",
    position: object,
    *,
    event_type: str,
    reason: str,
    error: str,
    source_module: str = "src.execution.exit_lifecycle",
    extra_payload: dict | None = None,
) -> bool:
    """Re-export shim — delegates to src.state.canonical_write.transition_phase.

    See that module for full docstring and implementation.
    """
    from src.state.canonical_write import transition_phase as _tp

    return _tp(
        conn,
        position,
        event_type=event_type,
        reason=reason,
        error=error,
        source_module=source_module,
        extra_payload=extra_payload,
    )


def _float_or_none(v: Any) -> float | None:
    """Coerce to float if non-None, otherwise return None."""
    return float(v) if v is not None else None


def log_exit_lifecycle_event(
    conn: sqlite3.Connection,
    pos: object,
    *,
    event_type: str,
    reason: str = "",
    error: str = "",
    status: str = "",
    order_id: str | None = None,
    details: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> None:
    """Append sell-side lifecycle telemetry without changing exit authority."""
    payload = {
        "status": status or getattr(pos, "exit_state", ""),
        "exit_reason": getattr(pos, "exit_reason", "") or reason,
        "error": error or getattr(pos, "last_exit_error", ""),
        "retry_count": getattr(pos, "exit_retry_count", 0),
        "next_retry_at": getattr(pos, "next_exit_retry_at", ""),
        "last_exit_order_id": getattr(pos, "last_exit_order_id", ""),
    }
    if details:
        payload.update(details)
    if event_type in {
        "EXIT_ORDER_POSTED",
        "EXIT_ORDER_ATTEMPTED",
        "EXIT_ORDER_FILLED",
        "EXIT_ORDER_REJECTED",
        "EXIT_ORDER_VOIDED",
        "EXIT_RETRY_SCHEDULED",
        "EXIT_BACKOFF_EXHAUSTED",
    }:
        terminal_exec_status = None
        voided_at = None
        filled_at = None
        exit_has_fill_finality = event_type == "EXIT_ORDER_FILLED"
        if event_type == "EXIT_ORDER_FILLED":
            terminal_exec_status = "filled"
            filled_at = timestamp or getattr(pos, "last_exit_at", None) or datetime.now(timezone.utc).isoformat()
        elif event_type in {"EXIT_RETRY_SCHEDULED", "EXIT_BACKOFF_EXHAUSTED", "EXIT_ORDER_REJECTED", "EXIT_ORDER_VOIDED"}:
            terminal_exec_status = str(payload.get("status") or getattr(pos, "exit_state", "") or "rejected")
            voided_at = timestamp or datetime.now(timezone.utc).isoformat()
        elif event_type in {"EXIT_ORDER_ATTEMPTED", "EXIT_ORDER_POSTED"}:
            terminal_exec_status = str(payload.get("status") or status or "pending")
        posted_at = (
            timestamp
            or getattr(pos, "last_exit_at", None)
            or getattr(pos, "entered_at", None)
            or datetime.now(timezone.utc).isoformat()
        )
        submitted_price = None
        sell_result = payload.get("sell_result")
        if isinstance(sell_result, dict):
            submitted_price = sell_result.get("submitted_price")
        if submitted_price in (None, "") and event_type in {"EXIT_ORDER_POSTED", "EXIT_ORDER_ATTEMPTED"}:
            submitted_price = payload.get("current_market_price")
        log_execution_fact(
            conn,
            intent_id=_execution_intent_id(
                trade_id=getattr(pos, "trade_id", ""),
                order_role="exit",
                explicit_intent_id=f"{getattr(pos, 'trade_id', '')}:exit",
            ),
            position_id=getattr(pos, "trade_id", ""),
            order_role="exit",
            decision_id=str(getattr(pos, "decision_id", None) or "") or None,
            strategy_key=str(getattr(pos, "strategy_key", "") or getattr(pos, "strategy", "") or "") or None,
            posted_at=posted_at if event_type in {"EXIT_ORDER_POSTED", "EXIT_ORDER_ATTEMPTED"} else None,
            filled_at=filled_at,
            voided_at=voided_at,
            submitted_price=submitted_price,
            fill_price=float(payload["fill_price"]) if exit_has_fill_finality and payload.get("fill_price") is not None else None,
            shares=_float_or_none(
                payload.get("shares")
                if payload.get("shares") is not None
                else getattr(pos, "effective_shares", getattr(pos, "shares", None))
            ) if exit_has_fill_finality else None,
            fill_quality=None,
            venue_status=str(payload.get("status") or status or "") or None,
            terminal_exec_status=terminal_exec_status,
            clear_fill_fields=not exit_has_fill_finality,
        )



def log_exit_retry_event(
    conn: sqlite3.Connection,
    pos: object,
    *,
    reason: str,
    error: str = "",
    timestamp: str | None = None,
) -> None:
    """Append retry/backoff telemetry after exit retry state is updated."""
    event_type = "EXIT_BACKOFF_EXHAUSTED" if getattr(pos, "exit_state", "") == "backoff_exhausted" else "EXIT_RETRY_SCHEDULED"
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type=event_type,
        reason=reason,
        error=error,
        timestamp=timestamp,
    )


def log_pending_exit_status_event(
    conn: sqlite3.Connection,
    pos: object,
    *,
    status: str,
    timestamp: str | None = None,
) -> None:
    """Append fill-check telemetry for an already placed exit order."""
    event_type = "EXIT_FILL_CONFIRMED" if status == "CONFIRMED" else "EXIT_FILL_CHECKED"
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type=event_type,
        status=status,
        timestamp=timestamp,
    )


def log_exit_attempt_event(
    conn: sqlite3.Connection,
    pos: object,
    *,
    order_id: str,
    status: str,
    current_market_price: float,
    best_bid: float | None,
    shares: float,
    details: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> None:
    """Append sell-order attempt telemetry at placement time."""
    payload = {
        "status": status,
        "current_market_price": current_market_price,
        "best_bid": best_bid,
        "shares": shares,
    }
    if details:
        payload.update(details)
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_ORDER_ATTEMPTED",
        status=status,
        order_id=order_id,
        details=payload,
        timestamp=timestamp,
    )


def log_exit_fill_event(
    conn: sqlite3.Connection,
    pos: object,
    *,
    order_id: str,
    fill_price: float,
    current_market_price: float,
    best_bid: float | None,
    timestamp: str | None = None,
) -> None:
    """Append terminal sell-fill telemetry for live exits."""
    payload = {
        "status": "CONFIRMED",
        "fill_price": fill_price,
        "current_market_price": current_market_price,
        "best_bid": best_bid,
        "shares": getattr(pos, "effective_shares", getattr(pos, "shares", None)),
    }
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_ORDER_FILLED",
        status="CONFIRMED",
        order_id=order_id,
        details=payload,
        timestamp=timestamp,
    )


def log_exit_fill_check_error_event(
    conn: sqlite3.Connection,
    pos: object,
    *,
    order_id: str,
    timestamp: str | None = None,
) -> None:
    """Append telemetry when sell fill status cannot be read."""
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_FILL_CHECK_FAILED",
        status="",
        order_id=order_id,
        timestamp=timestamp,
    )


def log_exit_retry_released_event(conn: sqlite3.Connection, pos: object, *, timestamp: str | None = None) -> None:
    """Append telemetry when cooldown expires and exit can be re-evaluated."""
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type="EXIT_RETRY_RELEASED",
        status="ready",
        timestamp=timestamp,
    )


def log_pending_exit_recovery_event(
    conn: sqlite3.Connection,
    pos: object,
    *,
    event_type: str,
    reason: str,
    error: str,
    timestamp: str | None = None,
) -> None:
    """Append telemetry for recovery of malformed/stranded pending exits."""
    log_exit_lifecycle_event(
        conn,
        pos,
        event_type=event_type,
        reason=reason,
        error=error,
        timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# F3 admin queries (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F3, 2026-05-28)
# ---------------------------------------------------------------------------


def query_unclassified_authority_rows(conn) -> list:
    """Return position_current rows where fill_authority IS NULL.

    Used by backfill scripts and ops tooling to verify migration completeness.
    Each returned dict has keys: position_id, phase, updated_at.
    """
    rows = conn.execute(
        """
        SELECT position_id, phase, updated_at
          FROM position_current
         WHERE fill_authority IS NULL
         ORDER BY updated_at
        """
    ).fetchall()
    keys = ("position_id", "phase", "updated_at")
    return [dict(zip(keys, r)) for r in rows]


def report_authority_distribution(conn) -> dict:
    """Return count of position_current rows grouped by fill_authority.

    NULL rows indicate migration not yet run (unmigrated legacy).
    Key is the fill_authority string value (or None for NULL rows).
    """
    rows = conn.execute(
        """
        SELECT fill_authority, COUNT(*) AS cnt
          FROM position_current
         GROUP BY fill_authority
        """
    ).fetchall()
    return {r[0]: r[1] for r in rows}
