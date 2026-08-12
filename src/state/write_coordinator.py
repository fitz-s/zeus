"""Runtime DB write coordination primitives.

This module is the first slice of the runtime DB lock refactor. It deliberately
does not migrate production writers yet. The contract it establishes is:

* one writer gate per DB file, shared by LIVE and BULK writes;
* multi-DB leases acquire gates in canonical path order;
* only single-DB transactions are opened here, so this layer does not pretend
  independent SQLite connections are cross-file atomic.
"""

from __future__ import annotations

import contextlib
import enum
import fcntl
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping

from src.state.db_writer_lock import WriteClass


class DBIdentity(str, enum.Enum):
    """Canonical runtime DB identities managed by the coordinator."""

    FORECAST = "forecast"
    TRADE = "trade"
    WORLD = "world"


class WritePriority(str, enum.Enum):
    """Admission class for the process-shared writer turnstile.

    MONITOR registers a kernel-lock waiter reservation before waiting on the
    turnstile; RECOVERY_CRITICAL waits on the turnstile; BACKGROUND probes the
    reservation and turnstile nonblocking. This prevents BACKGROUND from
    overtaking a registered MONITOR, not a total order between waiters.
    """

    STANDARD = "standard"
    MONITOR = "monitor"
    RECOVERY_CRITICAL = "recovery_critical"
    BACKGROUND_RECOVERY = "background_recovery"
    MONITOR_CANONICAL = "monitor"


class TransactionMode(str, enum.Enum):
    """SQLite transaction begin modes supported by the coordinator."""

    IMMEDIATE = "IMMEDIATE"
    DEFERRED = "DEFERRED"


class WriteLeaseTimeout(TimeoutError):
    """Raised when a write lease cannot acquire every required DB gate in time."""


class _MonitorIntentYield(WriteLeaseTimeout):
    """Internal retry signal when MONITOR intent appears during acquisition."""


class CrossDatabaseTransactionUnsupported(RuntimeError):
    """Raised when a caller requests a fake multi-connection DB transaction."""


@dataclass(frozen=True)
class WriteLeaseTelemetry:
    """JSON-ready telemetry for a write lease or single-DB transaction."""

    owner: str
    db_set: tuple[str, ...]
    db_paths: tuple[str, ...]
    write_class: str
    priority: str
    wait_ms: float
    hold_ms: float
    commit_ms: float
    rows_changed: int | None
    deadline_ms: int | None
    max_hold_ms: int | None
    deadline_exceeded: bool
    hold_limit_exceeded: bool
    error: str | None = None


@dataclass
class _LeaseMetrics:
    commit_ms: float = 0.0
    rows_changed: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class WriteLease:
    """Acquired DB-set write lease."""

    owner: str
    db_set: tuple[DBIdentity, ...]
    db_paths: tuple[Path, ...]
    write_class: WriteClass
    priority: WritePriority
    acquired_at: float
    _metrics: _LeaseMetrics = field(repr=False)

    def record_commit(self, *, commit_ms: float, rows_changed: int | None) -> None:
        """Attach commit metrics before the lease emits telemetry."""

        self._metrics.commit_ms = max(0.0, commit_ms)
        self._metrics.rows_changed = rows_changed


def _sqlite_busy(exc: sqlite3.OperationalError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }:
        return True
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


@contextlib.contextmanager
def bounded_sqlite_write(
    conn: sqlite3.Connection,
    lease: WriteLease,
    *,
    max_hold_ms: int,
    clock: Callable[[], float] | None = None,
) -> Iterator[None]:
    """Bound SQLite lock wait by the remaining coordinator hold budget.

    A coordinator lease serializes participating writers, but it cannot stop a
    legacy/raw SQLite writer.  Without this fence a lease owner can wait on that
    writer for the connection's default 30 seconds and starve MONITOR.  Busy is
    therefore a retryable lease timeout, while every other SQL error retains its
    original type.  The caller's busy timeout is restored on every exit.
    """

    if max_hold_ms <= 0:
        raise ValueError("max_hold_ms must be positive")
    now = clock or time.monotonic
    remaining_ms = float(max_hold_ms) - max(
        0.0,
        (now() - lease.acquired_at) * 1_000.0,
    )
    bounded_ms = int(remaining_ms)
    if bounded_ms <= 0:
        raise WriteLeaseTimeout(
            f"SQLite write hold budget exhausted for owner={lease.owner}"
        )
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    previous_ms = int(row[0]) if row is not None else 0
    conn.execute(f"PRAGMA busy_timeout = {min(previous_ms, bounded_ms)}")
    try:
        yield
    except sqlite3.OperationalError as exc:
        if _sqlite_busy(exc):
            raise WriteLeaseTimeout(
                f"SQLite write deferred within hold budget for owner={lease.owner}"
            ) from exc
        raise
    finally:
        conn.execute(f"PRAGMA busy_timeout = {previous_ms}")


@dataclass(frozen=True)
class WriteTransaction:
    """Single-DB transaction yielded by ``WriteCoordinator.transaction``."""

    lease: WriteLease
    db: DBIdentity
    connection: sqlite3.Connection


@dataclass
class _AcquiredGate:
    db: DBIdentity
    db_path: Path
    lock_path: Path
    fd: int
    process_lock: threading.Lock
    publication_fd: int | None = None


def _coerce_write_class(write_class: WriteClass | str) -> WriteClass:
    if isinstance(write_class, WriteClass):
        return write_class
    return WriteClass(str(write_class).lower())


def _coerce_transaction_mode(mode: TransactionMode | str) -> TransactionMode:
    if isinstance(mode, TransactionMode):
        return mode
    return TransactionMode(str(mode).upper())


def _coerce_db_identity(db: DBIdentity | str) -> DBIdentity:
    if isinstance(db, DBIdentity):
        return db
    return DBIdentity(str(db).lower())


def _coerce_write_priority(priority: WritePriority | str) -> WritePriority:
    if isinstance(priority, WritePriority):
        return priority
    value = str(priority).lower()
    if value == "monitor_canonical":
        value = WritePriority.MONITOR.value
    return WritePriority(value)


def _resolve_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def unified_writer_lock_path(db_path: Path | str) -> Path:
    """Return the per-DB unified lock-file path.

    This intentionally omits LIVE/BULK from the filename. Priority class is
    scheduler metadata; it must not create a separate same-file writer lane.
    """

    resolved = _resolve_path(db_path)
    return resolved.with_name(resolved.name + ".writer-lock")


def writer_turnstile_path(db_path: Path | str) -> Path:
    """Return the lock-only turnstile path for one DB."""

    resolved = _resolve_path(db_path)
    return resolved.with_name(resolved.name + ".writer-turnstile")


def writer_monitor_waiter_path(db_path: Path | str) -> Path:
    """Return the lock-only reservation path for MONITOR waiters."""

    resolved = _resolve_path(db_path)
    return resolved.with_name(resolved.name + ".writer-monitor-waiters")


def writer_monitor_intent_path(db_path: Path | str) -> Path:
    """Return the crash-safe cross-process MONITOR intent path."""

    resolved = _resolve_path(db_path)
    return resolved.with_name(resolved.name + ".writer-monitor-intent")


def _priority_uses_turnstile(priority: WritePriority) -> bool:
    return priority is not WritePriority.STANDARD


class WriteCoordinator:
    """Coordinate runtime DB write intent before SQLite transactions begin."""

    def __init__(
        self,
        db_paths: Mapping[DBIdentity | str, Path | str],
        *,
        telemetry_sink: Callable[[WriteLeaseTelemetry], None] | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if not db_paths:
            raise ValueError("WriteCoordinator requires at least one DB path")
        self._db_paths = {
            _coerce_db_identity(db): _resolve_path(path)
            for db, path in db_paths.items()
        }
        self._telemetry_sink = telemetry_sink
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._process_locks = {
            path: threading.Lock() for path in set(self._db_paths.values())
        }
        self._pending_monitor_waiters_lock = threading.Lock()
        self._pending_monitor_waiters = {
            path: 0 for path in set(self._db_paths.values())
        }

    def has_pending_monitor_waiter(
        self,
        dbs: Iterable[DBIdentity | str],
    ) -> bool:
        """Return whether a MONITOR writer is waiting for any requested DB.

        The kernel reservation prevents a new background lease from overtaking
        a monitor.  The separate intent lock closes the other half of that
        contract: a background pass that already owns the reservation can see
        a newly queued monitor in this process or another process and
        cooperatively end its current SQL quantum.
        """

        ordered = self.canonical_db_order(dbs)
        with self._pending_monitor_waiters_lock:
            if any(
                self._pending_monitor_waiters[self._db_paths[db]] > 0
                for db in ordered
            ):
                return True
        return any(
            self._monitor_intent_locked(self._db_paths[db])
            for db in ordered
        )

    @staticmethod
    def _monitor_intent_locked(db_path: Path) -> bool:
        path = writer_monitor_intent_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    @staticmethod
    def _acquire_monitor_intents(
        ordered: tuple[DBIdentity, ...],
        db_paths: Mapping[DBIdentity, Path],
    ) -> list[int]:
        fds: list[int] = []
        try:
            for db in ordered:
                path = writer_monitor_intent_path(db_paths[db])
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH)
                except BaseException:
                    os.close(fd)
                    raise
                fds.append(fd)
            return fds
        except BaseException:
            for fd in reversed(fds):
                WriteCoordinator._release_turnstile(fd)
            raise

    def _mark_monitor_waiting(
        self,
        ordered: tuple[DBIdentity, ...],
        delta: int,
    ) -> None:
        with self._pending_monitor_waiters_lock:
            for db in ordered:
                path = self._db_paths[db]
                next_count = self._pending_monitor_waiters[path] + delta
                if next_count < 0:
                    raise RuntimeError("monitor waiter accounting underflow")
                self._pending_monitor_waiters[path] = next_count

    def canonical_db_order(
        self,
        dbs: Iterable[DBIdentity | str],
    ) -> tuple[DBIdentity, ...]:
        """Return the unique DB set sorted by canonical resolved path."""

        unique = {_coerce_db_identity(db) for db in dbs}
        if not unique:
            raise ValueError("write lease requires at least one DB")
        missing = [db.value for db in unique if db not in self._db_paths]
        if missing:
            raise KeyError(f"DB path not configured for: {', '.join(sorted(missing))}")
        return tuple(
            sorted(unique, key=lambda db: (str(self._db_paths[db]), db.value))
        )

    @contextlib.contextmanager
    def lease(
        self,
        dbs: Iterable[DBIdentity | str],
        *,
        owner: str,
        write_class: WriteClass | str = WriteClass.LIVE,
        priority: WritePriority | str = WritePriority.STANDARD,
        deadline_ms: int | None = None,
        max_hold_ms: int | None = None,
    ) -> Iterator[WriteLease]:
        """Acquire unified write gates for the DB set, then emit telemetry."""

        if not owner:
            raise ValueError("owner is required for DB write leases")
        resolved_class = _coerce_write_class(write_class)
        resolved_priority = _coerce_write_priority(priority)
        ordered = self.canonical_db_order(dbs)
        started = self._clock()
        deadline = (
            None if deadline_ms is None else started + max(0, deadline_ms) / 1000.0
        )
        acquired: list[_AcquiredGate] = []
        metrics = _LeaseMetrics()
        acquired_at: float | None = None
        timeout_error: WriteLeaseTimeout | None = None
        try:
            monitor_waiting = resolved_priority is WritePriority.MONITOR
            monitor_intent_fds: list[int] = []
            if monitor_waiting:
                self._mark_monitor_waiting(ordered, 1)
            try:
                if monitor_waiting:
                    monitor_intent_fds = self._acquire_monitor_intents(
                        ordered,
                        self._db_paths,
                    )
                while True:
                    try:
                        acquired = self._acquire_gates(
                            ordered,
                            deadline=deadline,
                            owner=owner,
                            priority=resolved_priority,
                        )
                        break
                    except _MonitorIntentYield:
                        if (
                            resolved_priority is WritePriority.BACKGROUND_RECOVERY
                            or (deadline is not None and self._clock() >= deadline)
                        ):
                            raise
                        sleep_for = 0.01
                        if deadline is not None:
                            sleep_for = max(
                                0.001,
                                min(sleep_for, deadline - self._clock()),
                            )
                        self._sleep(sleep_for)
            finally:
                if monitor_waiting:
                    try:
                        for fd in reversed(monitor_intent_fds):
                            self._release_turnstile(fd)
                    finally:
                        self._mark_monitor_waiting(ordered, -1)
            acquired_at = self._clock()
            lease = WriteLease(
                owner=owner,
                db_set=ordered,
                db_paths=tuple(self._db_paths[db] for db in ordered),
                write_class=resolved_class,
                priority=resolved_priority,
                acquired_at=acquired_at,
                _metrics=metrics,
            )
            self._publish_nonmonitor_lease(acquired)
            try:
                yield lease
            except BaseException as exc:
                metrics.error = type(exc).__name__
                raise
        except WriteLeaseTimeout as exc:
            timeout_error = exc
            metrics.error = type(exc).__name__
            raise
        finally:
            released_at = self._clock()
            if acquired:
                self._release_gates(acquired)
            if self._telemetry_sink is not None:
                self._emit_telemetry(
                    owner=owner,
                    ordered=ordered,
                    write_class=resolved_class,
                    priority=resolved_priority,
                    started=started,
                    acquired_at=acquired_at,
                    released_at=released_at,
                    deadline_ms=deadline_ms,
                    max_hold_ms=max_hold_ms,
                    metrics=metrics,
                    deadline_exceeded=timeout_error is not None,
                )

    @contextlib.contextmanager
    def transaction(
        self,
        dbs: Iterable[DBIdentity | str],
        *,
        owner: str,
        write_class: WriteClass | str = WriteClass.LIVE,
        priority: WritePriority | str = WritePriority.STANDARD,
        deadline_ms: int | None = None,
        max_hold_ms: int | None = None,
        mode: TransactionMode | str = TransactionMode.IMMEDIATE,
        connection_factory: Callable[[Path], sqlite3.Connection] | None = None,
    ) -> Iterator[WriteTransaction]:
        """Open a coordinated single-DB transaction.

        Multi-DB leases are supported by ``lease``. Multi-DB transactions are not
        supported here because independent SQLite connections are not one
        crash-atomic transaction. Future migrations must either use a single
        attached connection with explicit schema ownership or a durable outbox.
        """

        ordered = self.canonical_db_order(dbs)
        if len(ordered) != 1:
            names = ", ".join(db.value for db in ordered)
            raise CrossDatabaseTransactionUnsupported(
                "WriteCoordinator.transaction supports one DB only; "
                f"requested DB set: {names}"
            )
        db = ordered[0]
        tx_mode = _coerce_transaction_mode(mode)
        factory = connection_factory or _default_connection_factory
        with self.lease(
            (db,),
            owner=owner,
            write_class=write_class,
            priority=priority,
            deadline_ms=deadline_ms,
            max_hold_ms=max_hold_ms,
        ) as lease:
            conn = factory(self._db_paths[db])
            before_changes = int(conn.total_changes)
            began = False
            try:
                with bounded_sqlite_write(
                    conn,
                    lease,
                    max_hold_ms=max_hold_ms,
                    clock=self._clock,
                ) if max_hold_ms is not None else contextlib.nullcontext():
                    conn.execute(f"BEGIN {tx_mode.value}")
                    began = True
                    yield WriteTransaction(lease=lease, db=db, connection=conn)
                    commit_started = self._clock()
                    conn.commit()
                    commit_ms = (self._clock() - commit_started) * 1000.0
                    rows_changed = max(0, int(conn.total_changes) - before_changes)
                    lease.record_commit(
                        commit_ms=commit_ms,
                        rows_changed=rows_changed,
                    )
            except BaseException:
                if began:
                    conn.rollback()
                raise
            finally:
                conn.close()

    def _acquire_gates(
        self,
        ordered: tuple[DBIdentity, ...],
        *,
        deadline: float | None,
        owner: str,
        priority: WritePriority,
    ) -> list[_AcquiredGate]:
        acquired: list[_AcquiredGate] = []
        nonmonitor_reservations: dict[DBIdentity, int] = {}
        try:
            if priority is not WritePriority.MONITOR:
                nonmonitor_reservations = self._acquire_nonmonitor_reservations(
                    ordered,
                    deadline=deadline,
                    owner=owner,
                    priority=priority,
                )
            for db in ordered:
                db_path = self._db_paths[db]
                process_lock = self._process_locks[db_path]
                monitor_waiter_fd: int | None = None
                turnstile_fd: int | None = None
                file_fd: int | None = None
                process_acquired = False
                try:
                    if priority is WritePriority.MONITOR:
                        monitor_waiter_fd = self._acquire_monitor_waiter_reservation(
                            db_path,
                            deadline=deadline,
                            db=db,
                            owner=owner,
                        )
                    elif priority is WritePriority.BACKGROUND_RECOVERY:
                        monitor_waiter_fd = nonmonitor_reservations.pop(db)
                    else:
                        monitor_waiter_fd = nonmonitor_reservations.pop(db)
                    if _priority_uses_turnstile(priority):
                        turnstile_fd = self._acquire_turnstile(
                            db_path,
                            deadline=deadline,
                            db=db,
                            owner=owner,
                            blocking=priority is not WritePriority.BACKGROUND_RECOVERY,
                        )
                    if (
                        priority is not WritePriority.MONITOR
                        and self._monitor_intent_locked(db_path)
                    ):
                        raise _MonitorIntentYield(
                            "DB writer yielded after turnstile for "
                            f"owner={owner}: monitor intent visible"
                        )
                    background = priority is WritePriority.BACKGROUND_RECOVERY
                    self._acquire_process_lock(
                        process_lock,
                        deadline=deadline,
                        db=db,
                        owner=owner,
                        blocking=not background,
                    )
                    process_acquired = True
                    file_fd = self._acquire_file_lock(
                        db_path,
                        deadline=deadline,
                        db=db,
                        owner=owner,
                        blocking=not background,
                    )
                    if (
                        priority is not WritePriority.MONITOR
                        and self._monitor_intent_locked(db_path)
                    ):
                        raise _MonitorIntentYield(
                            "DB writer yielded before reservation release for "
                            f"owner={owner}: monitor intent visible"
                        )
                except BaseException:
                    self._cleanup_current_gate(
                        turnstile_fd=turnstile_fd,
                        monitor_waiter_fd=monitor_waiter_fd,
                        file_fd=file_fd,
                        process_lock=process_lock,
                        process_acquired=process_acquired,
                        release_file=True,
                        release_process=True,
                    )
                    raise
                cleanup_error = self._cleanup_current_gate(
                    turnstile_fd=turnstile_fd,
                    monitor_waiter_fd=monitor_waiter_fd,
                    file_fd=file_fd,
                    process_lock=process_lock,
                    process_acquired=process_acquired,
                    release_file=False,
                    release_process=False,
                )
                if cleanup_error is not None:
                    self._cleanup_current_gate(
                        turnstile_fd=None,
                        monitor_waiter_fd=None,
                        file_fd=file_fd,
                        process_lock=process_lock,
                        process_acquired=process_acquired,
                        release_file=True,
                        release_process=True,
                    )
                    raise cleanup_error
                acquired.append(
                    _AcquiredGate(
                        db=db,
                        db_path=db_path,
                        lock_path=unified_writer_lock_path(db_path),
                        fd=file_fd,
                        process_lock=process_lock,
                    )
                )
            if priority is not WritePriority.MONITOR and any(
                self._monitor_intent_locked(self._db_paths[db])
                for db in ordered
            ):
                raise _MonitorIntentYield(
                    "DB writer yielded before DB-set publication for "
                    f"owner={owner}: monitor intent visible"
                )
            if priority is not WritePriority.MONITOR:
                publication_fds = self._acquire_nonmonitor_publication_barrier(
                    ordered,
                    owner=owner,
                )
                for gate, publication_fd in zip(
                    acquired,
                    publication_fds,
                    strict=True,
                ):
                    gate.publication_fd = publication_fd
            return acquired
        except BaseException as exc:
            cleanup_error = self._release_turnstile_set(
                reversed(tuple(nonmonitor_reservations.values()))
            )
            try:
                self._release_gates(acquired)
            except BaseException as release_exc:
                if cleanup_error is None:
                    cleanup_error = release_exc
            if isinstance(exc, WriteLeaseTimeout) and cleanup_error is not None:
                raise cleanup_error from exc
            raise

    def _acquire_nonmonitor_publication_barrier(
        self,
        ordered: tuple[DBIdentity, ...],
        *,
        owner: str,
    ) -> list[int]:
        """Linearize a non-monitor lease against new MONITOR intent.

        The exclusive intent locks remain held after every DB gate is owned and
        until ``lease`` has built the public lease object.  A MONITOR therefore
        either publishes intent before this barrier and makes the writer yield,
        or publishes after the non-monitor lease has linearized.  There is no
        check-to-publication window in which both can believe they won.
        """

        fds: list[int] = []
        try:
            for db in ordered:
                path = writer_monitor_intent_path(self._db_paths[db])
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    os.close(fd)
                    raise _MonitorIntentYield(
                        "DB writer yielded at lease publication for "
                        f"owner={owner}: monitor intent visible"
                    ) from exc
                except BaseException:
                    os.close(fd)
                    raise
                fds.append(fd)
            return fds
        except BaseException:
            cleanup_error = self._release_turnstile_set(reversed(fds))
            if cleanup_error is not None:
                raise cleanup_error
            raise

    def _publish_nonmonitor_lease(self, acquired: list[_AcquiredGate]) -> None:
        """Release intent barriers only after gate ownership is publishable."""

        first_error: BaseException | None = None
        for gate in reversed(acquired):
            if gate.publication_fd is None:
                continue
            try:
                self._release_turnstile(gate.publication_fd)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
            finally:
                gate.publication_fd = None
        if first_error is not None:
            raise first_error

    def _release_turnstile_set(self, fds) -> BaseException | None:
        """Release every reservation even when one close path faults."""

        first_error: BaseException | None = None
        for fd in fds:
            try:
                self._release_turnstile(fd)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        return first_error

    def _acquire_nonmonitor_reservations(
        self,
        ordered: tuple[DBIdentity, ...],
        *,
        deadline: float | None,
        owner: str,
        priority: WritePriority,
    ) -> dict[DBIdentity, int]:
        """Acquire a non-monitor DB set atomically with respect to MONITOR.

        A writer must not hold one DB gate while waiting for another DB's
        monitor reservation.  Acquire the complete reservation set first; on
        any conflict, release the partial set before retrying.  A MONITOR that
        publishes intent during the sweep therefore cannot form a cross-DB
        lock-order cycle with this writer.
        """

        background = priority is WritePriority.BACKGROUND_RECOVERY
        while True:
            reservations: dict[DBIdentity, int] = {}
            try:
                if any(
                    self._monitor_intent_locked(self._db_paths[db])
                    for db in ordered
                ):
                    raise WriteLeaseTimeout(
                        "DB writer monitor waiter reservation deferred "
                        f"for owner={owner}: monitor intent visible"
                    )
                for db in ordered:
                    reservations[db] = self._acquire_nonmonitor_reservation(
                        self._db_paths[db],
                        deadline=self._clock(),
                        db=db,
                        owner=owner,
                        blocking=False,
                    )
                if any(
                    self._monitor_intent_locked(self._db_paths[db])
                    for db in ordered
                ):
                    raise WriteLeaseTimeout(
                        "DB writer monitor waiter reservation deferred "
                        f"for owner={owner}: monitor intent visible"
                    )
                return reservations
            except BaseException as exc:
                cleanup_error = self._release_turnstile_set(
                    reversed(tuple(reservations.values()))
                )
                if not isinstance(exc, WriteLeaseTimeout):
                    raise
                if cleanup_error is not None:
                    raise cleanup_error from exc
                if background or (deadline is not None and self._clock() >= deadline):
                    raise
                sleep_for = 0.01
                if deadline is not None:
                    sleep_for = max(
                        0.001,
                        min(sleep_for, deadline - self._clock()),
                    )
                self._sleep(sleep_for)

    def _acquire_process_lock(
        self,
        lock: threading.Lock,
        *,
        deadline: float | None,
        db: DBIdentity,
        owner: str,
        blocking: bool,
    ) -> None:
        if not blocking:
            if not lock.acquire(blocking=False):
                raise WriteLeaseTimeout(
                    f"DB write lease deferred for owner={owner} db={db.value}"
                )
            return
        if deadline is None:
            lock.acquire()
            return
        remaining = deadline - self._clock()
        if remaining <= 0 or not lock.acquire(timeout=remaining):
            raise WriteLeaseTimeout(
                f"DB write lease timed out for owner={owner} db={db.value}"
            )

    def _acquire_file_lock(
        self,
        db_path: Path,
        *,
        deadline: float | None,
        db: DBIdentity,
        owner: str,
        blocking: bool,
    ) -> int:
        lock_path = unified_writer_lock_path(db_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError as exc:
                if not blocking:
                    os.close(fd)
                    raise WriteLeaseTimeout(
                        f"DB write lease deferred for owner={owner} db={db.value}"
                    ) from exc
                if deadline is not None and self._clock() >= deadline:
                    os.close(fd)
                    raise WriteLeaseTimeout(
                        f"DB write lease timed out for owner={owner} db={db.value}"
                    ) from exc
                sleep_for = 0.01
                if deadline is not None:
                    sleep_for = max(0.001, min(sleep_for, deadline - self._clock()))
                self._sleep(sleep_for)

    def _acquire_turnstile(
        self,
        db_path: Path,
        *,
        deadline: float | None,
        db: DBIdentity,
        owner: str,
        blocking: bool,
    ) -> int:
        path = writer_turnstile_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        owned = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    owned = True
                    return fd
                except BlockingIOError as exc:
                    if not blocking:
                        raise WriteLeaseTimeout(
                            f"DB writer turnstile deferred for owner={owner} db={db.value}"
                        ) from exc
                    if deadline is not None and self._clock() >= deadline:
                        raise WriteLeaseTimeout(
                            f"DB writer turnstile timed out for owner={owner} db={db.value}"
                        ) from exc
                    sleep_for = 0.01
                    if deadline is not None:
                        sleep_for = max(0.001, min(sleep_for, deadline - self._clock()))
                    self._sleep(sleep_for)
        finally:
            if not owned:
                os.close(fd)

    def _acquire_monitor_waiter_reservation(
        self,
        db_path: Path,
        *,
        deadline: float | None,
        db: DBIdentity,
        owner: str,
    ) -> int:
        path = writer_monitor_waiter_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        owned = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    owned = True
                    return fd
                except BlockingIOError as exc:
                    if deadline is not None and self._clock() >= deadline:
                        raise WriteLeaseTimeout(
                            f"DB monitor waiter reservation timed out for owner={owner} db={db.value}"
                        ) from exc
                    sleep_for = 0.01
                    if deadline is not None:
                        sleep_for = max(0.001, min(sleep_for, deadline - self._clock()))
                    self._sleep(sleep_for)
        finally:
            if not owned:
                os.close(fd)

    def _acquire_nonmonitor_reservation(
        self,
        db_path: Path,
        *,
        deadline: float | None,
        db: DBIdentity,
        owner: str,
        blocking: bool,
    ) -> int:
        path = writer_monitor_waiter_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        owned = False
        transferred = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    owned = True
                except BlockingIOError as exc:
                    if not blocking:
                        raise WriteLeaseTimeout(
                            "DB writer monitor waiter reservation deferred "
                            f"for owner={owner} db={db.value}"
                        ) from exc
                    if deadline is not None and self._clock() >= deadline:
                        raise WriteLeaseTimeout(
                            "DB writer monitor waiter reservation timed out "
                            f"for owner={owner} db={db.value}"
                        ) from exc
                else:
                    # A MONITOR publishes its separate crash-safe intent before
                    # waiting on this reservation.  A non-monitor that won the
                    # reservation race must yield it when that intent is now
                    # visible, closing the check/acquire race for STANDARD and
                    # RECOVERY_CRITICAL writers as well as BACKGROUND.
                    if not self._monitor_intent_locked(db_path):
                        transferred = True
                        return fd
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        owned = False
                    if not blocking:
                        raise WriteLeaseTimeout(
                            "DB writer monitor intent deferred "
                            f"for owner={owner} db={db.value}"
                        )
                    if deadline is not None and self._clock() >= deadline:
                        raise WriteLeaseTimeout(
                            "DB writer monitor intent timed out "
                            f"for owner={owner} db={db.value}"
                        )
                sleep_for = 0.01
                if deadline is not None:
                    sleep_for = max(
                        0.001,
                        min(sleep_for, deadline - self._clock()),
                    )
                self._sleep(sleep_for)
        finally:
            if not transferred:
                if owned:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except BaseException:
                        pass
                os.close(fd)

    def _acquire_background_reservation(
        self,
        db_path: Path,
        *,
        db: DBIdentity,
        owner: str,
    ) -> int:
        return self._acquire_nonmonitor_reservation(
            db_path,
            deadline=self._clock(),
            db=db,
            owner=owner,
            blocking=False,
        )

    @staticmethod
    def _release_turnstile(fd: int) -> None:
        first_error = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BaseException as exc:
            first_error = exc
        try:
            os.close(fd)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            raise first_error

    def _cleanup_current_gate(
        self,
        *,
        turnstile_fd: int | None,
        monitor_waiter_fd: int | None,
        file_fd: int | None,
        process_lock: threading.Lock,
        process_acquired: bool,
        release_file: bool,
        release_process: bool,
    ) -> BaseException | None:
        first_error: BaseException | None = None

        def attempt(cleanup: Callable[[], None]) -> None:
            nonlocal first_error
            try:
                cleanup()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if turnstile_fd is not None:
            attempt(lambda: self._release_turnstile(turnstile_fd))
        if monitor_waiter_fd is not None:
            attempt(lambda: self._release_turnstile(monitor_waiter_fd))
        if release_file and file_fd is not None:
            def release_file() -> None:
                file_error = None
                try:
                    fcntl.flock(file_fd, fcntl.LOCK_UN)
                except BaseException as exc:
                    file_error = exc
                try:
                    os.close(file_fd)
                except BaseException as exc:
                    if file_error is None:
                        file_error = exc
                if file_error is not None:
                    raise file_error

            attempt(release_file)
        if release_process and process_acquired:
            attempt(process_lock.release)
        return first_error

    def _release_gates(self, acquired: list[_AcquiredGate]) -> None:
        first_error: BaseException | None = None
        for gate in reversed(acquired):
            if gate.publication_fd is not None:
                try:
                    self._release_turnstile(gate.publication_fd)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                finally:
                    gate.publication_fd = None
            try:
                fcntl.flock(gate.fd, fcntl.LOCK_UN)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
            try:
                os.close(gate.fd)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
            try:
                gate.process_lock.release()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _emit_telemetry(
        self,
        *,
        owner: str,
        ordered: tuple[DBIdentity, ...],
        write_class: WriteClass,
        priority: WritePriority,
        started: float,
        acquired_at: float | None,
        released_at: float,
        deadline_ms: int | None,
        max_hold_ms: int | None,
        metrics: _LeaseMetrics,
        deadline_exceeded: bool,
    ) -> None:
        wait_stop = acquired_at if acquired_at is not None else released_at
        hold_ms = 0.0 if acquired_at is None else (released_at - acquired_at) * 1000.0
        telemetry = WriteLeaseTelemetry(
            owner=owner,
            db_set=tuple(db.value for db in ordered),
            db_paths=tuple(str(self._db_paths[db]) for db in ordered),
            write_class=write_class.value,
            priority=priority.value,
            wait_ms=max(0.0, (wait_stop - started) * 1000.0),
            hold_ms=max(0.0, hold_ms),
            commit_ms=metrics.commit_ms,
            rows_changed=metrics.rows_changed,
            deadline_ms=deadline_ms,
            max_hold_ms=max_hold_ms,
            deadline_exceeded=deadline_exceeded,
            hold_limit_exceeded=(
                max_hold_ms is not None and hold_ms > float(max_hold_ms)
            ),
            error=metrics.error,
        )
        self._telemetry_sink(telemetry)


def _default_connection_factory(path: Path) -> sqlite3.Connection:
    from src.state.db import _connect

    return _connect(path, write_class=None)


_DEFAULT_RUNTIME_COORDINATOR: WriteCoordinator | None = None
_DEFAULT_RUNTIME_COORDINATOR_LOCK = threading.Lock()


def default_runtime_write_coordinator() -> WriteCoordinator:
    """Return the process-global coordinator for canonical runtime DB files."""

    global _DEFAULT_RUNTIME_COORDINATOR
    if _DEFAULT_RUNTIME_COORDINATOR is not None:
        return _DEFAULT_RUNTIME_COORDINATOR
    with _DEFAULT_RUNTIME_COORDINATOR_LOCK:
        if _DEFAULT_RUNTIME_COORDINATOR is None:
            from src.state.db import (
                ZEUS_FORECASTS_DB_PATH,
                ZEUS_WORLD_DB_PATH,
                _zeus_trade_db_path,
            )

            _DEFAULT_RUNTIME_COORDINATOR = WriteCoordinator(
                {
                    DBIdentity.FORECAST: ZEUS_FORECASTS_DB_PATH,
                    DBIdentity.TRADE: _zeus_trade_db_path(),
                    DBIdentity.WORLD: ZEUS_WORLD_DB_PATH,
                },
                telemetry_sink=_counter_telemetry_sink,
            )
        return _DEFAULT_RUNTIME_COORDINATOR


def _counter_telemetry_sink(row: WriteLeaseTelemetry) -> None:
    from src.observability.counters import increment

    labels = {
        "db_set": ",".join(row.db_set),
        "owner": row.owner,
        "write_class": row.write_class,
        "priority": row.priority,
    }
    increment("db_write_lease_total", labels=labels)
    if row.deadline_exceeded:
        increment("db_write_lease_timeout_total", labels=labels)
    if row.hold_limit_exceeded:
        increment("db_write_lease_hold_limit_exceeded_total", labels=labels)
