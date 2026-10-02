"""What a process read from SQLite, digested from the rows its own cursors yielded.

SCOPE: one worker invocation. Connections opened inside ``recordable()`` (the
worker's long-lived ones) or while a ``SQLiteReadRecorder`` is active (any it
opens on the way) get the recording cursor; while a recorder is active, each
read statement (SELECT, WITH, VALUES, a getter PRAGMA) executed on any of them
becomes an entry: (database files, SQL, parameters, rows consumed, whether the
cursor was exhausted, sha256 of exactly the rows the caller received). Nothing
is executed twice: the digest grows as the caller fetches.
DRAIN: the parent re-executes each entry against the same files and digests the
same number of rows (and, for an exhausted cursor, proves there is no further
row): equal digests mean the state the worker judged is the current state,
"no row" included. RESET: any changed digest.
A read that cannot be reproduced (a connection without the recording cursor, a
memory or temp database, a nondeterministic statement, a row of unknown shape,
a fetch error) makes the witness incomplete, and an incomplete witness binds
nothing.
"""

# Created: 2026-10-01
# Last reused/audited: 2026-10-01
# Authority basis: merge-safety round 6 BLOCKER 1 (database-dependent verdict binding).

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
import re
import sqlite3
import threading
from typing import Iterable

_NONDETERMINISTIC = re.compile(
    r"\b(random|randomblob|changes|total_changes|last_insert_rowid)\s*\(|'now'"
    r"|\bcurrent_(time|date|timestamp)\b",
    re.I,
)
_ACTIVE: "SQLiteReadRecorder | None" = None
_LOCK = threading.Lock()


def _is_read(sql: str) -> bool:
    head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
    if head in ("SELECT", "WITH", "VALUES"):
        return True
    return head == "PRAGMA" and "=" not in sql and "data_version" not in sql.lower()


def _value(value: object) -> object:
    if isinstance(value, bytes):
        return ("b", value.hex())
    if isinstance(value, float):
        return ("f", value.hex())
    return value


def _row_values(row: object) -> tuple | None:
    if isinstance(row, (tuple, sqlite3.Row)):
        return tuple(row)
    if isinstance(row, Mapping):
        return tuple(row.values())
    return None


def _param(value: object) -> object:
    if isinstance(value, bytes):
        return {"b": value.hex()}
    if isinstance(value, float):
        return {"f": value.hex()}
    if value is None or isinstance(value, (str, int)):
        return value
    raise TypeError("parameter")


def _unparam(value: object) -> object:
    if isinstance(value, dict) and "b" in value:
        return bytes.fromhex(value["b"])
    if isinstance(value, dict) and "f" in value:
        return float.fromhex(value["f"])
    return value


def _params(params: object) -> object:
    if isinstance(params, Mapping):
        return {"named": {str(k): _param(v) for k, v in params.items()}}
    return {"positional": [_param(v) for v in (params or ())]}


def _bind(params: Mapping[str, object]) -> object:
    if "named" in params:
        return {k: _unparam(v) for k, v in params["named"].items()}
    return [_unparam(v) for v in params["positional"]]


class _Read:
    __slots__ = ("databases", "sql", "params", "count", "exhausted", "digest")

    def __init__(self, databases, sql, params) -> None:
        self.databases, self.sql, self.params = databases, sql, params
        self.count, self.exhausted, self.digest = 0, False, hashlib.sha256()

    def add(self, row: object) -> bool:
        values = _row_values(row)
        if values is None:
            return False
        self.digest.update(repr(tuple(_value(v) for v in values)).encode("utf-8"))
        self.digest.update(b"\n")
        self.count += 1
        return True


class RecordingCursor(sqlite3.Cursor):
    """A cursor that digests what it hands its caller while a recorder is active."""

    _read: _Read | None = None

    def execute(self, sql, parameters=(), /):
        result = super().execute(sql, parameters)
        self._read = None
        recorder = _ACTIVE
        if recorder is not None and _is_read(sql):
            self._read = recorder._begin(self.connection, sql, parameters)
        return result

    def _yield(self, rows: list, *, exhausted: bool) -> list:
        read = self._read
        if read is not None:
            for row in rows:
                if not read.add(row):
                    _fail("row of unknown shape")
                    self._read = None
                    return rows
            if exhausted:
                read.exhausted = True
        return rows

    def fetchone(self):
        row = super().fetchone()
        if self._read is not None:
            self._yield([] if row is None else [row], exhausted=row is None)
        return row

    def fetchmany(self, size=None):
        rows = super().fetchmany(self.arraysize if size is None else size)
        if self._read is not None:
            self._yield(rows, exhausted=not rows)
        return rows

    def fetchall(self):
        rows = super().fetchall()
        if self._read is not None:
            self._yield(rows, exhausted=True)
        return rows

    def __next__(self):
        try:
            row = super().__next__()
        except StopIteration:
            if self._read is not None:
                self._read.exhausted = True
            raise
        if self._read is not None:
            self._yield([row], exhausted=False)
        return row


class RecordingConnection(sqlite3.Connection):
    """A connection whose reads a ``SQLiteReadRecorder`` can see."""

    def cursor(self, factory=RecordingCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=(), /):
        return self.cursor().execute(sql, parameters)


def _fail(reason: str) -> None:
    recorder = _ACTIVE
    if recorder is not None and recorder.incomplete is None:
        recorder.incomplete = reason


_RECORDING_FACTORIES: dict[type, type] = {sqlite3.Connection: RecordingConnection}


def _recording(factory: type) -> type:
    """``factory`` with recording reads composed in (its own behaviour kept)."""
    if issubclass(factory, RecordingConnection):
        return factory
    composed = _RECORDING_FACTORIES.get(factory)
    if composed is None:
        composed = type(f"Recording{factory.__name__}", (RecordingConnection, factory), {})
        _RECORDING_FACTORIES[factory] = composed
    return composed


_ORIGINAL_CONNECT = sqlite3.connect
_PATCH_DEPTH = 0


def _connect(*args, **kwargs):
    """``sqlite3.connect`` inside ``recordable``/a recorder: the default factory and
    any ``sqlite3.Connection`` subclass gain the recording cursor (behaviour
    kept). The cursor digests nothing unless a recorder is active."""
    factory = kwargs.get("factory", sqlite3.Connection)
    if isinstance(factory, type) and issubclass(factory, sqlite3.Connection):
        kwargs["factory"] = _recording(factory)
    conn = _ORIGINAL_CONNECT(*args, **kwargs)
    recorder = _ACTIVE
    if recorder is not None:
        recorder.watch(conn)
    return conn


def _patch(on: bool) -> None:
    global _PATCH_DEPTH
    _PATCH_DEPTH += 1 if on else -1
    sqlite3.connect = _connect if _PATCH_DEPTH > 0 else _ORIGINAL_CONNECT


@contextmanager
def recordable():
    """Connections opened in this block can later report reads to a recorder."""
    with _LOCK:
        _patch(True)
    try:
        yield
    finally:
        with _LOCK:
            _patch(False)


def _databases(conn: sqlite3.Connection) -> tuple[tuple[str, str], ...] | None:
    rows = tuple(
        (str(row[1]), str(row[2] or ""))
        for row in sqlite3.Connection.execute(conn, "PRAGMA database_list").fetchall()
    )
    if any(name != "temp" and not path for name, path in rows):
        return None  # a memory database cannot be re-read by anyone else
    return tuple((name, path) for name, path in rows if name != "temp")


class SQLiteReadRecorder:
    """Collect the reads of one invocation (see module doc). One active at a time."""

    def __init__(self) -> None:
        self.reads: list[_Read] = []
        self.incomplete: str | None = None
        self._databases: dict[int, tuple[tuple[str, str], ...] | None] = {}
        self._blind: list[sqlite3.Connection] = []

    def watch(self, conn: sqlite3.Connection) -> None:
        """A connection that cannot digest its reads may still be used for writes:
        it is traced, and its first read makes the witness incomplete."""
        if isinstance(conn, RecordingConnection) or any(conn is c for c in self._blind):
            return
        self._blind.append(conn)

        def trace(sql: str) -> None:
            if _ACTIVE is self and _is_read(sql) and self.incomplete is None:
                self.incomplete = "read on a connection that does not record reads"

        conn.set_trace_callback(trace)

    def _begin(self, conn: sqlite3.Connection, sql: str, parameters: object) -> _Read | None:
        try:
            key = id(conn)
            if key not in self._databases:
                self._databases[key] = _databases(conn)
            databases = self._databases[key]
            if databases is None:
                raise ValueError("memory database")
            if _NONDETERMINISTIC.search(sql) or re.search(r"\btemp\.", sql, re.I):
                raise ValueError("statement not reproducible")
            read = _Read(databases, sql, _params(parameters))
        except Exception as exc:  # noqa: BLE001 - an unreproducible read is incomplete
            if self.incomplete is None:
                self.incomplete = str(exc) or type(exc).__name__
            return None
        self.reads.append(read)
        return read

    def __enter__(self) -> "SQLiteReadRecorder":
        # Connections opened inside this block record their reads; outside it,
        # sqlite3.connect is the untouched original.
        global _ACTIVE
        with _LOCK:
            if _ACTIVE is not None:
                raise RuntimeError("a read recorder is already active")
            _ACTIVE = self
            _patch(True)
        return self

    def __exit__(self, *_exc) -> None:
        global _ACTIVE
        with _LOCK:
            _ACTIVE = None
            _patch(False)
        for conn in self._blind:
            try:
                conn.set_trace_callback(None)
            except sqlite3.ProgrammingError:
                pass  # already closed

    def witness(self) -> dict[str, object]:
        entries = {
            (r.databases, r.sql, repr(r.params), r.count, r.exhausted, r.digest.hexdigest()): r
            for r in self.reads
        }
        return {
            "complete": self.incomplete is None,
            "incomplete_reason": self.incomplete,
            "reads": [
                {"databases": [list(pair) for pair in r.databases], "sql": r.sql,
                 "params": r.params, "rows": r.count, "exhausted": r.exhausted,
                 "digest": key[5]}
                for key, r in sorted(entries.items(), key=lambda item: item[0][:5])
            ],
        }


def _identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("database name")
    return '"' + name + '"'


def _reproduces(conn: sqlite3.Connection, entry: Mapping[str, object]) -> bool:
    read = _Read((), "", {})
    cursor = sqlite3.Connection.cursor(conn)
    try:
        cursor.execute(str(entry["sql"]), _bind(entry["params"]))
        wanted = int(entry["rows"])
        for row in cursor:
            if read.count == wanted:
                return not entry["exhausted"]  # a further row the worker never saw
            read.add(row)
        return read.count == wanted and read.digest.hexdigest() == entry["digest"]
    finally:
        cursor.close()


def database_reads_reproduce(witness: Mapping[str, object] | None) -> bool:
    """Whether every recorded read returns, now, exactly the rows it returned then."""
    if not isinstance(witness, Mapping) or witness.get("complete") is not True:
        return False
    reads = witness.get("reads")
    if not isinstance(reads, (list, tuple)):
        return False
    connections: dict[tuple[tuple[str, str], ...], sqlite3.Connection] = {}
    try:
        for entry in reads:
            databases = tuple((str(name), str(path)) for name, path in entry["databases"])
            conn = connections.get(databases)
            if conn is None:
                main = dict(databases).get("main")
                if not main:
                    return False
                conn = sqlite3.Connection(f"file:{main}?mode=ro", uri=True)
                connections[databases] = conn
                for name, path in databases:
                    if name != "main":
                        conn.execute(
                            "ATTACH DATABASE ? AS " + _identifier(name), (f"file:{path}?mode=ro",)
                        )
            # Same-content row, different digest would need a hash collision; a
            # matching digest over a fresh read is the state the worker judged.
            if not _reproduces(conn, entry):
                return False
    except (sqlite3.Error, KeyError, TypeError, ValueError):
        return False
    finally:
        for conn in connections.values():
            conn.close()
    return True


def rows_digest(rows: Iterable[Iterable[object]]) -> str:
    read = _Read((), "", {})
    for row in rows:
        read.add(tuple(row))
    return read.digest.hexdigest()
