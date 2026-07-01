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


class TransactionMode(str, enum.Enum):
    """SQLite transaction begin modes supported by the coordinator."""

    IMMEDIATE = "IMMEDIATE"
    DEFERRED = "DEFERRED"


class WriteLeaseTimeout(TimeoutError):
    """Raised when a write lease cannot acquire every required DB gate in time."""


class CrossDatabaseTransactionUnsupported(RuntimeError):
    """Raised when a caller requests a fake multi-connection DB transaction."""


@dataclass(frozen=True)
class WriteLeaseTelemetry:
    """JSON-ready telemetry for a write lease or single-DB transaction."""

    owner: str
    db_set: tuple[str, ...]
    db_paths: tuple[str, ...]
    write_class: str
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
    acquired_at: float
    _metrics: _LeaseMetrics = field(repr=False)

    def record_commit(self, *, commit_ms: float, rows_changed: int | None) -> None:
        """Attach commit metrics before the lease emits telemetry."""

        self._metrics.commit_ms = max(0.0, commit_ms)
        self._metrics.rows_changed = rows_changed


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


def _resolve_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def unified_writer_lock_path(db_path: Path | str) -> Path:
    """Return the per-DB unified lock-file path.

    This intentionally omits LIVE/BULK from the filename. Priority class is
    scheduler metadata; it must not create a separate same-file writer lane.
    """

    resolved = _resolve_path(db_path)
    return resolved.with_name(resolved.name + ".writer-lock")


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
        deadline_ms: int | None = None,
        max_hold_ms: int | None = None,
    ) -> Iterator[WriteLease]:
        """Acquire unified write gates for the DB set, then emit telemetry."""

        if not owner:
            raise ValueError("owner is required for DB write leases")
        resolved_class = _coerce_write_class(write_class)
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
            acquired = self._acquire_gates(ordered, deadline=deadline, owner=owner)
            acquired_at = self._clock()
            lease = WriteLease(
                owner=owner,
                db_set=ordered,
                db_paths=tuple(self._db_paths[db] for db in ordered),
                write_class=resolved_class,
                acquired_at=acquired_at,
                _metrics=metrics,
            )
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
            deadline_ms=deadline_ms,
            max_hold_ms=max_hold_ms,
        ) as lease:
            conn = factory(self._db_paths[db])
            before_changes = int(conn.total_changes)
            began = False
            try:
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
    ) -> list[_AcquiredGate]:
        acquired: list[_AcquiredGate] = []
        try:
            for db in ordered:
                db_path = self._db_paths[db]
                process_lock = self._process_locks[db_path]
                self._acquire_process_lock(
                    process_lock,
                    deadline=deadline,
                    db=db,
                    owner=owner,
                )
                try:
                    fd = self._acquire_file_lock(
                        db_path,
                        deadline=deadline,
                        db=db,
                        owner=owner,
                    )
                except BaseException:
                    process_lock.release()
                    raise
                acquired.append(
                    _AcquiredGate(
                        db=db,
                        db_path=db_path,
                        lock_path=unified_writer_lock_path(db_path),
                        fd=fd,
                        process_lock=process_lock,
                    )
                )
            return acquired
        except BaseException:
            self._release_gates(acquired)
            raise

    def _acquire_process_lock(
        self,
        lock: threading.Lock,
        *,
        deadline: float | None,
        db: DBIdentity,
        owner: str,
    ) -> None:
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
    ) -> int:
        lock_path = unified_writer_lock_path(db_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError as exc:
                if deadline is not None and self._clock() >= deadline:
                    os.close(fd)
                    raise WriteLeaseTimeout(
                        f"DB write lease timed out for owner={owner} db={db.value}"
                    ) from exc
                sleep_for = 0.01
                if deadline is not None:
                    sleep_for = max(0.001, min(sleep_for, deadline - self._clock()))
                self._sleep(sleep_for)

    def _release_gates(self, acquired: list[_AcquiredGate]) -> None:
        for gate in reversed(acquired):
            try:
                fcntl.flock(gate.fd, fcntl.LOCK_UN)
            finally:
                try:
                    os.close(gate.fd)
                finally:
                    gate.process_lock.release()

    def _emit_telemetry(
        self,
        *,
        owner: str,
        ordered: tuple[DBIdentity, ...],
        write_class: WriteClass,
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
    }
    increment("db_write_lease_total", labels=labels)
    if row.deadline_exceeded:
        increment("db_write_lease_timeout_total", labels=labels)
    if row.hold_limit_exceeded:
        increment("db_write_lease_hold_limit_exceeded_total", labels=labels)
