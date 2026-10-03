# Created: 2026-07-02
# Last reused/audited: 2026-08-23
# Authority basis: architecture/invariants.yaml
#   section 1 row "q_version + input HWMs (A1)".
"""Shared consumed-proof validation and separate successor refresh debt.

A new raw/model/ensemble cycle does not invalidate a certified whole posterior.
Readers consume intrinsic invalidity; builders additionally consume refresh debt.
No posterior clock, probability, or original submission witness is rewritten.
"""

from __future__ import annotations

import functools
import hashlib
import json
import marshal
import os
import re
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from src.data.market_topology_rows import (
    _database_names,
    _table_ref_columns,
    _table_ref_exists,
    _table_ref_indexes,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import (
    PRODUCT_ID as OPENMETEO_ANCHOR_PRODUCT_ID,
    SOURCE_ID as OPENMETEO_ANCHOR_SOURCE_ID,
)

UTC = timezone.utc

# (channel: None for the caller's connection or the read-only database URI,
#  statement, parameters, rows consumed, exhausted, sha256 of those rows)
RecordedReads = tuple[tuple[tuple[object, ...], ...], tuple[tuple[str, object], ...]]

# Valid consumed-proof verdicts and the current station ground they were
# judged against, keyed on every non-clock input they read. Invalid or
# unknown outcomes are never remembered; the LRU bound caps the process.
_MEMO_LIMIT = 2048
_VERDICT_MEMO: OrderedDict[tuple[object, ...], tuple[datetime, RecordedReads]] = OrderedDict()
_MEMO_LOCK = threading.Lock()

# A remembered verdict holds only while every read it made answers the same.
# No table is assumed append-only (source_run is replaced in place, snapshots
# can be overwritten), so the reads are recorded by construction, not listed:
# every SQL read on the caller's connection and on each read-only connection
# the verdict opens, with its parameters, the rows it consumed and a digest of
# them; and every file or directory it opens. A hit replays each read and
# re-stats each file; any difference, error or absence is a miss.
_RECORDS: ContextVar[tuple["ReadRecord", ...]] = ContextVar("hwm_read_records", default=())
# Inside fresh_source() no remembered verdict is served (see fresh_source).
_FRESH_SOURCE: ContextVar[bool] = ContextVar("hwm_fresh_source", default=False)
_CODE_SUFFIXES = (".py", ".pyc", ".pyi", ".so", ".dylib")
_READ_SQL = re.compile(
    r"\s*(?:SELECT|WITH)\b"
    r"|\s*PRAGMA\s+(?:\S+\.)?(?:table_x?info|index_list|index_x?info|database_list)\b",
    re.IGNORECASE,
)
_PRAGMA_SQL = re.compile(r"\s*PRAGMA\b", re.IGNORECASE)


@dataclass
class ReadRecord:
    # path -> its version when first opened inside the record. Taken at open,
    # not when the record is frozen, so an edit landing between the read and
    # the freeze is a different version, never the remembered one.
    files: dict[str, object] = field(default_factory=dict)
    reads: list[list[object]] = field(default_factory=list)
    replayable: bool = True

    def statement(self, channel: str | None, sql: str, parameters: object) -> list[object] | None:
        if _READ_SQL.match(sql):
            params = (dict(parameters) if isinstance(parameters, Mapping)
                      else tuple(parameters))
            read = [channel, sys.intern(sql), params, 0, False, hashlib.sha256()]
            self.reads.append(read)
            return read
        # A connection setting or PRAGMA data_version (the snapshot memo's own
        # commit clock) decides nothing; any other statement is not a read.
        if not _PRAGMA_SQL.match(sql):
            self.replayable = False
        return None

    def frozen(self) -> RecordedReads:
        return (
            tuple((c, s, p, n, x, h.digest()) for c, s, p, n, x, h in self.reads),
            tuple(sorted(self.files.items())),
        )


def _row_digest(digest: hashlib._Hash, row: object) -> None:
    # marshal v2 shares no references, so equal rows encode to equal bytes,
    # and it keeps int/float/str/bytes/None and -0.0 apart.
    digest.update(marshal.dumps(tuple(row), 2))


class _RecordingCursor(sqlite3.Cursor):
    """Digests each row it hands out, in order; a Row and a tuple agree."""

    def __init__(self, connection: sqlite3.Connection, *, channel: str | None,
                 records: tuple[ReadRecord, ...]) -> None:
        super().__init__(connection)
        self._channel, self._records, self._reads = channel, records, ()

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        reads = (record.statement(self._channel, sql, parameters) for record in self._records)
        self._reads = tuple(read for read in reads if read is not None)
        return super().execute(sql, parameters)

    def _seen(self, rows: list[object], *, exhausted: bool) -> list[object]:
        for read in self._reads:
            for row in rows:
                _row_digest(read[5], row)
            read[3] += len(rows)
            read[4] = read[4] or exhausted
        return rows

    def fetchone(self) -> object:
        row = super().fetchone()
        self._seen([] if row is None else [row], exhausted=row is None)
        return row

    def fetchmany(self, size: int | None = None) -> list[object]:
        size = self.arraysize if size is None else size
        rows = super().fetchmany(size)
        return self._seen(rows, exhausted=0 < size and len(rows) < size)

    def fetchall(self) -> list[object]:
        return self._seen(super().fetchall(), exhausted=True)

    def __next__(self) -> object:
        try:
            row = super().__next__()
        except StopIteration:
            self._seen([], exhausted=True)
            raise
        return self._seen([row], exhausted=False)[0]


class _RecordedReadOnlyConnection(sqlite3.Connection):
    """A read-only connection opened inside a recorded verdict."""

    def __init__(self, database: str, *args: object, records: tuple[ReadRecord, ...],
                 **kwargs: object) -> None:
        super().__init__(database, *args, **kwargs)
        self._channel, self._records = str(database), records

    def cursor(self, factory: object = None) -> sqlite3.Cursor:
        return super().cursor(functools.partial(
            _RecordingCursor, channel=self._channel, records=self._records))

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        return self.cursor().execute(sql, parameters)


class _RecordedConnectionView:
    """The caller's connection as a recorded verdict sees it."""

    def __init__(self, conn: sqlite3.Connection, records: tuple[ReadRecord, ...]) -> None:
        self._conn, self._records = conn, records

    def cursor(self) -> sqlite3.Cursor:
        return self._conn.cursor(functools.partial(
            _RecordingCursor, channel=None, records=self._records))

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        return self.cursor().execute(sql, parameters)

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def _record_file_read(event: str, args: tuple[object, ...]) -> None:
    # Installed once at import; the sink is this context's _RECORDS, so with
    # no record active (every other caller, every other thread) it returns at
    # once, and another thread's open never lands in a record.
    records = _RECORDS.get()
    if not records or not args:
        return
    if event == "sqlite3.connect/handle":
        # A connection not opened through the recording factory reads unseen.
        if type(args[0]) is not _RecordedReadOnlyConnection:
            for record in records:
                record.replayable = False
    elif event in ("_thread.start_joinable_thread", "_thread.start_new_thread"):
        # A worker thread does not inherit the record, so its reads would go
        # unseen. The read-only opener's deadline thread is the one exception:
        # it opens with the factory captured here and reads nothing itself.
        thread = getattr(args[0], "__self__", None)
        if getattr(thread, "name", None) != "zeus-read-only-deadline-open":
            for record in records:
                record.replayable = False
    elif event in ("open", "os.scandir", "os.listdir") and isinstance(
        args[0], (str, bytes, os.PathLike)
    ):
        _note_file(args[0])


sys.addaudithook(_record_file_read)


def _note_file(path: str | bytes | os.PathLike) -> None:
    """Add ``path`` and its current version to every active record; a
    stat-keyed cache answering without opening its file names it here."""
    resolved = os.path.abspath(os.fsdecode(path))
    if resolved.endswith(_CODE_SUFFIXES):
        return
    for record in _RECORDS.get():
        if resolved not in record.files:
            record.files[resolved] = _file_identity(resolved)


@contextmanager
def fresh_source():
    """Prove every consumed-proof and live-grade verdict from source inside.

    Actuation revalidation runs here: for a Day0 ENTRY or a SELL it is the
    last consumed-authority check before the venue (the executor's own check
    covers only non-Day0 ENTRY), so it never rests on a remembered verdict.
    Usable as a decorator.
    """
    token = _FRESH_SOURCE.set(True)
    try:
        yield
    finally:
        _FRESH_SOURCE.reset(token)


@contextmanager
def recorded_reads(record: ReadRecord | None, conn: sqlite3.Connection | None = None):
    """Record every read in the block into ``record``; yields ``conn`` as seen.

    ``None`` records nothing and yields ``conn`` itself. Pass memos are
    suspended inside, so no read is answered from outside the record.
    """
    if record is None:
        yield conn
        return
    from src.data.openmeteo_model_surface import _SURFACE_READ_PASS
    from src.data.replacement_current_value_serving import _PHYSICAL_READ_PASS
    from src.state.db import READ_ONLY_CONNECTION_FACTORY

    records = (*_RECORDS.get(), record)
    tokens = (
        (_RECORDS, _RECORDS.set(records)),
        (READ_ONLY_CONNECTION_FACTORY, READ_ONLY_CONNECTION_FACTORY.set(
            functools.partial(_RecordedReadOnlyConnection, records=records))),
        (_PHYSICAL_READ_PASS, _PHYSICAL_READ_PASS.set(None)),
        (_SURFACE_READ_PASS, _SURFACE_READ_PASS.set(None)),
    )
    try:
        yield None if conn is None else _RecordedConnectionView(conn, records)
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def _answer(conn: sqlite3.Connection, sql: str, params: object, count: int) -> tuple[bytes, bool]:
    """(sha256 of the first ``count`` rows, whether another row follows)."""
    cursor = conn.execute(sql, params)
    try:
        rows = cursor.fetchmany(count) if count else []
        seen = hashlib.sha256()
        for row in rows:
            _row_digest(seen, row)
        return (seen.digest() if len(rows) == count else b""), cursor.fetchone() is not None
    finally:
        cursor.close()


_SNAPSHOT_ANSWERS = threading.local()


def _snapshot_answers(conn: sqlite3.Connection) -> dict[tuple[object, ...], tuple[bytes, bool]]:
    """Replay answers for one never-written connection's visible snapshot.

    The rule of replacement_current_value_serving._snapshot_memo: every
    schema's data_version moves when another connection commits and holds
    inside a read transaction, as the visible rows do; a connection that wrote
    could roll its own rows back without moving it, so it shares nothing.
    """
    if conn.total_changes:
        return {}
    versions = tuple(
        conn.execute(f'PRAGMA "{name.replace(chr(34), chr(34) * 2)}".data_version').fetchone()[0]
        for _seq, name, *_rest in conn.execute("PRAGMA database_list").fetchall()
    )
    held = getattr(_SNAPSHOT_ANSWERS, "held", None)
    if held is None or held[0] is not conn or held[1] != versions:
        held = _SNAPSHOT_ANSWERS.held = (conn, versions, {})
    return held[2]


def reads_hold(recorded: RecordedReads, conn: sqlite3.Connection | None = None) -> bool:
    """Whether every recorded file is unchanged and every read answers the same.

    Reads on ``conn`` are answered once per visible snapshot: verdicts of one
    cut share capture-group reads, and an unchanged snapshot answers them alike.
    """
    from src.state.db import _connect_read_only

    reads, files = recorded
    if file_fingerprints(path for path, _ in files) != files:
        return False
    opened: dict[str, sqlite3.Connection] = {}
    try:
        answers = _snapshot_answers(conn) if conn is not None else {}
        for channel, sql, params, count, exhausted, digest in reads:
            if channel is None:
                if conn is None:
                    return False
                key = (sql, tuple((type(v), v) for v in (
                    params.items() if isinstance(params, dict) else params)), count)
                answer = answers.get(key)
                if answer is None:
                    answer = answers[key] = _answer(conn, sql, params, count)
            else:
                if channel not in opened:
                    opened[channel] = _connect_read_only(
                        Path(channel.removeprefix("file:").rsplit("?", 1)[0]))
                answer = _answer(opened[channel], sql, params, count)
            if answer[0] != digest or (exhausted and answer[1]):
                return False
        return True
    except (sqlite3.Error, OSError, ValueError):
        return False
    finally:
        for opened_conn in opened.values():
            opened_conn.close()


def _file_identity(path: str) -> object:
    """The file version a remembered verdict depends on, or None if absent.

    versioned_file_read.file_version (dev, ino, size, mtime_ns, ctime_ns),
    the semantics the model-surface and seed readers already use: no utime()
    can restore ctime and an atomic replace moves the inode, so equal versions
    mean equal bytes. lstat, plus the mode, also tells a symlink swap apart.
    """
    from src.data.versioned_file_read import file_version

    try:
        info = os.lstat(path)
    except OSError:
        return None
    return (info.st_mode, *file_version(info))


def file_fingerprints(paths: Iterable[str]) -> tuple[tuple[str, object], ...]:
    """(path, identity) per path; any change or absence alters it."""
    return tuple((path, _file_identity(path)) for path in sorted(paths))


def _memo_get(memo: OrderedDict, key: tuple[object, ...]) -> object | None:
    with _MEMO_LOCK:
        value = memo.get(key)
        if value is not None:
            memo.move_to_end(key)
        return value


def _memo_put(memo: OrderedDict, key: tuple[object, ...], value: object) -> None:
    with _MEMO_LOCK:
        memo[key] = value
        memo.move_to_end(key)
        while len(memo) > _MEMO_LIMIT:
            memo.popitem(last=False)


def clear_consumed_proof_memo() -> None:
    with _MEMO_LOCK:
        _VERDICT_MEMO.clear()


def authority_config_identity() -> tuple[str, float]:
    """The configuration the consumed-proof and live-grade verdicts read.

    City fields and station-coordinate/ground claims (one manifest), and the
    source-cycle age horizon the certificate checks replay.
    """
    from src.config import runtime_coordinate_manifest_json
    from src.data.replacement_forecast_cycle_policy import replacement_source_cycle_max_age_hours

    return (
        hashlib.sha256(runtime_coordinate_manifest_json().encode()).hexdigest(),
        replacement_source_cycle_max_age_hours(),
    )


def provenance_identity(provenance: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        provenance, sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()


class ReplacementInputHwmReadUnavailable(sqlite3.OperationalError):
    """The exact raw-input HWM read could not establish current authority."""

    def __init__(
        self,
        message: str,
        *,
        basis: str = "replacement_input_hwm_read_unavailable",
    ) -> None:
        super().__init__(message)
        self.basis = basis

    def blocker_reason(self) -> str:
        return f"basis={self.basis}:sqlite_error={self}"


def _is_transient_sqlite_read_error(exc: sqlite3.OperationalError) -> bool:
    transient_codes = {
        code
        for code in (
            getattr(sqlite3, "SQLITE_BUSY", None),
            getattr(sqlite3, "SQLITE_LOCKED", None),
            getattr(sqlite3, "SQLITE_INTERRUPT", None),
        )
        if isinstance(code, int)
    }
    error_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(error_code, int) and (error_code & 0xFF) in transient_codes:
        return True
    if getattr(exc, "sqlite_errorname", None) in {
        "SQLITE_BUSY",
        "SQLITE_LOCKED",
        "SQLITE_INTERRUPT",
    }:
        return True

    message = str(exc).strip().lower()
    if message in {
        "interrupted",
        "database is locked",
        "database table is locked",
        "database schema is locked",
        "database is busy",
        "sqlite_read_deadline_exceeded",
        "sqlite_read_cancelled",
        "sqlite_read_canceled",
    }:
        return True
    return any(
        message.startswith(prefix) and bool(message.removeprefix(prefix).strip())
        for prefix in (
            "database table is locked:",
            "database schema is locked:",
        )
    )


def _raise_hwm_read_unavailable(
    exc: sqlite3.OperationalError,
    *,
    basis: str,
) -> None:
    if _is_transient_sqlite_read_error(exc):
        raise ReplacementInputHwmReadUnavailable(
            str(exc),
            basis=basis,
        ) from exc
    raise exc


def _raise_hwm_deadline_elapsed(*, basis: str) -> None:
    raise ReplacementInputHwmReadUnavailable(
        "replacement input HWM read deadline elapsed",
        basis=basis,
    )


@dataclass
class _HwmProgressController:
    """Keep one SQL deadline across a streaming cursor."""

    conn: sqlite3.Connection
    callback: Callable[[], int]
    enabled: bool = True
    installed: bool = False

    def install(self) -> None:
        if not self.enabled:
            return
        self.conn.set_progress_handler(self.callback, 1_000)
        self.installed = True

    def suspend(self) -> None:
        if self.enabled and self.installed:
            self.conn.set_progress_handler(None, 0)
            self.installed = False

    def resume(self) -> None:
        if self.enabled and not self.installed:
            self.install()


@contextmanager
def _bounded_hwm_sql(
    conn: sqlite3.Connection,
    deadline_monotonic: float | None,
    sql_timeout_seconds: float | None,
):
    """Bound one SQL statement on a dedicated HWM read connection."""

    if deadline_monotonic is None and sql_timeout_seconds is None:
        yield _HwmProgressController(conn, lambda: 0, enabled=False)
        return
    started = time.monotonic()
    outer_deadline = (
        None if deadline_monotonic is None else float(deadline_monotonic)
    )
    remaining = (
        float("inf")
        if outer_deadline is None
        else outer_deadline - started
    )
    sql_cpu_deadline = None
    if sql_timeout_seconds is not None:
        sql_timeout = max(0.0, float(sql_timeout_seconds))
        remaining = min(remaining, sql_timeout)
        sql_cpu_deadline = time.thread_time() + sql_timeout
    if remaining <= 0.0:
        _raise_hwm_deadline_elapsed(
            basis="raw_artifact_input_hwm_sql_deadline",
        )
    previous_busy_timeout_row = conn.execute("PRAGMA busy_timeout").fetchone()
    previous_busy_timeout_ms = int(
        (previous_busy_timeout_row[0] if previous_busy_timeout_row else 0) or 0
    )

    def deadline_elapsed() -> bool:
        return bool(
            (outer_deadline is not None and time.monotonic() >= outer_deadline)
            or (
                sql_cpu_deadline is not None
                and time.thread_time() >= sql_cpu_deadline
            )
        )

    progress = _HwmProgressController(conn, deadline_elapsed)
    try:
        lock_wait_seconds = min(1.0, remaining)
        conn.execute(
            "PRAGMA busy_timeout = "
            f"{max(0, int(lock_wait_seconds * 1000))}"
        )
        progress.install()
        yield progress
        if deadline_elapsed():
            _raise_hwm_deadline_elapsed(
                basis="raw_artifact_input_hwm_sql_deadline",
            )
    except ReplacementInputHwmReadUnavailable:
        raise
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="raw_artifact_input_hwm_read_unavailable",
        )
    finally:
        try:
            progress.suspend()
        finally:
            conn.execute(f"PRAGMA busy_timeout = {previous_busy_timeout_ms}")


def _require_hwm_deadline(
    deadline_monotonic: float | None,
    *,
    basis: str,
) -> None:
    if (
        deadline_monotonic is not None
        and time.monotonic() >= float(deadline_monotonic)
    ):
        _raise_hwm_deadline_elapsed(basis=basis)


def _bounded_artifact_table_ref(
    conn: sqlite3.Connection,
    *,
    deadline_monotonic: float | None,
    sql_timeout_seconds: float | None,
) -> str | None:
    with _bounded_hwm_sql(conn, deadline_monotonic, sql_timeout_seconds):
        attached = _database_names(conn)
    for candidate in (
        *(("forecasts.raw_forecast_artifacts",) if "forecasts" in attached else ()),
        *(("world.raw_forecast_artifacts",) if "world" in attached else ()),
        "raw_forecast_artifacts",
    ):
        with _bounded_hwm_sql(conn, deadline_monotonic, sql_timeout_seconds):
            if _table_ref_exists(conn, candidate):
                return candidate
    return None


def _bounded_hwm_table_ref_columns(
    conn: sqlite3.Connection,
    table_ref: str,
    *,
    deadline_monotonic: float | None,
    sql_timeout_seconds: float | None,
) -> frozenset[str]:
    with _bounded_hwm_sql(conn, deadline_monotonic, sql_timeout_seconds):
        return _hwm_table_ref_columns(conn, table_ref)


def _hwm_table_ref_columns(
    conn: sqlite3.Connection,
    table_ref: str,
) -> frozenset[str]:
    try:
        return frozenset(_table_ref_columns(conn, table_ref))
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="replacement_input_hwm_schema_read_unavailable",
        )


@dataclass(frozen=True)
class _FrozenInputHwm:
    conn: sqlite3.Connection | None
    decision_iso: str
    requests: frozenset[tuple[str, str, str]]
    artifact_loaded: bool
    artifact_cycles: Mapping[tuple[str, str, str], datetime]
    blocker_reason: str | None = None


_FROZEN_INPUT_HWM: ContextVar[_FrozenInputHwm | None] = ContextVar(
    "replacement_frozen_input_hwm",
    default=None,
)


def _parse_source_cycle_utc(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _latest_utc_timestamp(*values: object) -> datetime | None:
    parsed = [_parse_source_cycle_utc(value) for value in values]
    present = [value for value in parsed if value is not None]
    return max(present) if present else None


def _authority_table_ref(conn: sqlite3.Connection, table_name: str) -> str | None:
    try:
        attached = _database_names(conn)
        if "forecasts" in attached:
            if _table_ref_exists(conn, f"forecasts.{table_name}"):
                return f"forecasts.{table_name}"
        # The materializer opens forecasts as MAIN and attaches WORLD for
        # observations. WORLD's legacy names must not shadow canonical inputs.
        if _table_ref_exists(conn, f"main.{table_name}"):
            return table_name
        if "world" in attached:
            if _table_ref_exists(conn, f"world.{table_name}"):
                return f"world.{table_name}"
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="replacement_input_hwm_table_lookup_unavailable",
        )
    return None


def ensemble_source_authority_sql(
    *,
    ensemble_alias: str,
    source_run_ref: str,
    source_run_clock_columns: tuple[str, ...],
    coverage_ref: str | None,
    coverage_identity_index: str | None = None,
    decision_time: datetime,
) -> tuple[str, tuple[object, ...]]:
    """Build the shared decision-time ENS source-authority predicate.

    Every run needs exact target-local-day coverage for the selected snapshot.
    A run-level ``SUCCESS`` proves transport completion, not that this city,
    target day, metric, member set, and required step geometry are usable.
    The coverage row must be COMPLETE/LIVE_ELIGIBLE, contain every required
    step and expected member, and be durably recorded before the decision cut.
    """

    run_clock_expr = (
        f"source_run.{source_run_clock_columns[0]}"
        if len(source_run_clock_columns) == 1
        else "COALESCE("
        + ", ".join(
            f"source_run.{column}" for column in source_run_clock_columns
        )
        + ")"
    )

    decision_iso = decision_time.astimezone(UTC).isoformat()
    target_coverage = "0"
    coverage_params: tuple[object, ...] = ()
    if coverage_ref is not None:
        coverage_index_clause = ""
        if coverage_identity_index is not None:
            if not coverage_identity_index.replace("_", "").isalnum():
                raise ValueError("source-run coverage index identity is invalid")
            coverage_index_clause = f" INDEXED BY {coverage_identity_index}"
        target_coverage = f"""
                source_run.status IN ('PARTIAL', 'SUCCESS')
                AND source_run.completeness_status IN ('PARTIAL', 'COMPLETE')
                AND EXISTS (
                    SELECT 1
                      FROM {coverage_ref} AS source_coverage{coverage_index_clause}
                     WHERE source_coverage.source_run_id = source_run.source_run_id
                       AND source_coverage.source_id = 'ecmwf_open_data'
                       AND source_coverage.release_calendar_key = source_run.release_calendar_key
                       AND source_coverage.track = source_run.track
                       AND lower(source_coverage.city) = lower({ensemble_alias}.city)
                       AND source_coverage.target_local_date = {ensemble_alias}.target_date
                       AND source_coverage.temperature_metric = {ensemble_alias}.temperature_metric
                       AND source_coverage.completeness_status = 'COMPLETE'
                       AND source_coverage.readiness_status = 'LIVE_ELIGIBLE'
                       AND source_coverage.expected_members > 0
                       AND source_coverage.observed_members >= source_coverage.expected_members
                       AND datetime(source_coverage.computed_at) <= datetime(?)
                       AND datetime(source_coverage.recorded_at) <= datetime(?)
                       AND source_coverage.expires_at IS NOT NULL
                       AND datetime(source_coverage.expires_at) > datetime(?)
                       AND json_valid(source_coverage.expected_steps_json)
                       AND json_valid(source_coverage.observed_steps_json)
                       AND json_array_length(source_coverage.expected_steps_json) > 0
                       AND source_coverage.observed_steps_json = source_coverage.expected_steps_json
                       AND json_valid(source_coverage.snapshot_ids_json)
                       AND json_array_length(source_coverage.snapshot_ids_json) = 1
                       AND CAST(json_extract(source_coverage.snapshot_ids_json, '$[0]') AS TEXT)
                           = CAST({ensemble_alias}.snapshot_id AS TEXT)
                )
        """
        coverage_params = (decision_iso, decision_iso, decision_iso)

    return (
        f"""
        EXISTS (
            SELECT 1
              FROM {source_run_ref} AS source_run
             WHERE source_run.source_run_id = {ensemble_alias}.source_run_id
               AND datetime({run_clock_expr}) <= datetime(?)
               AND ({target_coverage})
        )
        """,
        (decision_iso, *coverage_params),
    )


def ensemble_source_authority_predicate(
    conn: sqlite3.Connection,
    *,
    ensemble_alias: str,
    decision_time: datetime,
) -> tuple[str, tuple[object, ...]] | None:
    """Bind the shared ENS predicate to the available authority tables."""

    source_run_ref = _authority_table_ref(conn, "source_run")
    if source_run_ref is None:
        return None
    source_run_columns = _hwm_table_ref_columns(conn, source_run_ref)
    required_run_columns = {
        "source_run_id",
        "status",
        "completeness_status",
        "partial_run",
    }
    clock_columns = tuple(
        column
        for column in (
            "imported_at",
            "fetch_finished_at",
            "captured_at",
            "source_available_at",
        )
        if column in source_run_columns
    )
    if not required_run_columns.issubset(source_run_columns) or not clock_columns:
        return None

    coverage_ref = _authority_table_ref(conn, "source_run_coverage")
    coverage_identity_index = None
    if coverage_ref is not None:
        coverage_columns = _hwm_table_ref_columns(conn, coverage_ref)
        required_coverage_columns = {
            "source_run_id",
            "source_id",
            "release_calendar_key",
            "track",
            "city",
            "target_local_date",
            "temperature_metric",
            "expected_members",
            "observed_members",
            "expected_steps_json",
            "observed_steps_json",
            "snapshot_ids_json",
            "completeness_status",
            "readiness_status",
            "computed_at",
            "expires_at",
            "recorded_at",
        }
        if not required_coverage_columns.issubset(coverage_columns):
            coverage_ref = None
        else:
            schema, table = (
                coverage_ref.split(".", 1)
                if "." in coverage_ref
                else ("main", coverage_ref)
            )
            if all(part.replace("_", "").isalnum() for part in (schema, table)):
                coverage_identity_index = next(
                    (
                        index_name
                        for index_name, columns in _table_ref_indexes(
                            conn, f"{schema}.{table}"
                        )
                        if columns[:2] == ("source_run_id", "source_id")
                    ),
                    None,
                )
    return ensemble_source_authority_sql(
        ensemble_alias=ensemble_alias,
        source_run_ref=source_run_ref,
        source_run_clock_columns=clock_columns,
        coverage_ref=coverage_ref,
        coverage_identity_index=coverage_identity_index,
        decision_time=decision_time,
    )


def latest_raw_model_input_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
) -> datetime | None:
    decision_iso = decision_time.astimezone(UTC).isoformat()
    table_ref = _authority_table_ref(conn, "raw_model_forecasts")
    if table_ref is None:
        return None
    columns = _hwm_table_ref_columns(conn, table_ref)
    required = {"model", "city", "target_date", "metric", "source_cycle_time"}
    if not required.issubset(columns):
        return None
    predicates = ["city = ?", "target_date = ?", "metric = ?"]
    params: list[object] = [city, target_date, metric]
    if "endpoint" in columns:
        predicates.append("endpoint = 'single_runs'")
    if "coverage_status" in columns:
        predicates.append("(coverage_status IS NULL OR coverage_status = 'COVERED')")
    if "captured_at" in columns:
        predicates.append("(captured_at IS NULL OR datetime(captured_at) <= datetime(?))")
        params.append(decision_iso)
    if "source_available_at" in columns:
        predicates.append(
            "(source_available_at IS NULL OR datetime(source_available_at) <= datetime(?))"
        )
        params.append(decision_iso)
    anchor_terms = ["model = 'ecmwf_ifs'"]
    if "source_id" in columns:
        anchor_terms.append("source_id = 'ecmwf_ifs_single_runs'")
    if "product_id" in columns:
        anchor_terms.append("product_id = 'ecmwf_ifs::single_runs'")
    anchor_expr = " OR ".join(anchor_terms)
    try:
        row = conn.execute(
            f"""
            SELECT source_cycle_time
              FROM {table_ref}
             WHERE {' AND '.join(predicates)}
               AND datetime(source_cycle_time) <= datetime(?)
             GROUP BY source_cycle_time
             HAVING COUNT(DISTINCT model) >= 2
                AND SUM(CASE WHEN ({anchor_expr}) THEN 1 ELSE 0 END) > 0
             ORDER BY datetime(source_cycle_time) DESC
             LIMIT 1
            """,
            tuple([*params, decision_iso]),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="raw_model_input_hwm_read_unavailable",
        )
    if row is None:
        return None
    try:
        raw_value = row["source_cycle_time"]
    except Exception:  # noqa: BLE001
        raw_value = row[0]
    return _parse_source_cycle_utc(raw_value)


def latest_raw_artifact_input_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
) -> datetime | None:
    decision_iso = decision_time.astimezone(UTC).isoformat()
    key = (city, str(target_date), metric)
    frozen = _FROZEN_INPUT_HWM.get()
    if (
        frozen is not None
        and (frozen.conn is None or frozen.conn is conn)
        and frozen.decision_iso == decision_iso
        and key in frozen.requests
    ):
        if frozen.blocker_reason:
            raise ReplacementInputHwmReadUnavailable(
                frozen.blocker_reason,
                basis="frozen_artifact_input_hwm_prefetch_unavailable",
            )
        if not frozen.artifact_loaded:
            return None
        return frozen.artifact_cycles.get(key)
    table_ref = _authority_table_ref(conn, "raw_forecast_artifacts")
    if table_ref is None:
        return None
    columns = _hwm_table_ref_columns(conn, table_ref)
    required = {
        "source_cycle_time",
        "captured_at",
        "source_available_at",
        "artifact_metadata_json",
    }
    if not required.issubset(columns):
        return None
    if {"source_id", "product_id"}.issubset(columns):
        try:
            data_version_row = conn.execute("PRAGMA data_version").fetchone()
        except sqlite3.OperationalError as exc:
            _raise_hwm_read_unavailable(
                exc,
                basis="raw_artifact_input_hwm_read_unavailable",
            )
        try:
            data_version = int(data_version_row[0]) if data_version_row is not None else -1
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        try:
            return _raw_artifact_cycle_for_frozen_request(
                conn,
                table_ref,
                frozenset(columns),
                key,
                decision_iso,
                data_version,
                conn.total_changes,
            )
        except sqlite3.OperationalError as exc:
            _raise_hwm_read_unavailable(
                exc,
                basis="raw_artifact_input_hwm_read_unavailable",
            )
    predicates = [
        "json_extract(artifact_metadata_json, '$.city') = ?",
        "json_extract(artifact_metadata_json, '$.target_date') = ?",
        "json_extract(artifact_metadata_json, '$.metric') = ?",
        "datetime(captured_at) <= datetime(?)",
        "datetime(source_available_at) <= datetime(?)",
    ]
    params: list[object] = [
        city,
        target_date,
        metric,
        decision_iso,
        decision_iso,
    ]
    if "source_id" in columns:
        predicates.append("source_id = ?")
        params.append(OPENMETEO_ANCHOR_SOURCE_ID)
    if "product_id" in columns:
        predicates.append("product_id = ?")
        params.append(OPENMETEO_ANCHOR_PRODUCT_ID)
    can_verify_payload = "artifact_path" in columns
    select_payload = ", artifact_path" if can_verify_payload else ""
    if conn.in_transaction:
        try:
            data_version_row = conn.execute("PRAGMA data_version").fetchone()
        except sqlite3.OperationalError as exc:
            _raise_hwm_read_unavailable(
                exc,
                basis="raw_artifact_input_hwm_read_unavailable",
            )
        try:
            data_version = int(data_version_row[0]) if data_version_row is not None else -1
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        try:
            cached = dict(
                _raw_artifact_cycles_for_frozen_target(
                    conn,
                    table_ref,
                    frozenset(columns),
                    str(target_date),
                    metric,
                    decision_iso,
                    data_version,
                    conn.total_changes,
                )
            )
        except sqlite3.OperationalError as exc:
            _raise_hwm_read_unavailable(
                exc,
                basis="raw_artifact_input_hwm_read_unavailable",
            )
        return cached.get(city)
    try:
        rows = conn.execute(
            f"""
            SELECT source_cycle_time{select_payload}, artifact_metadata_json
              FROM {table_ref}
             WHERE {' AND '.join(predicates)}
               AND datetime(source_cycle_time) <= datetime(?)
             GROUP BY source_cycle_time
             ORDER BY datetime(source_cycle_time) DESC
            """,
            tuple([*params, decision_iso]),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="raw_artifact_input_hwm_read_unavailable",
        )
    for row in rows:
        try:
            raw_value = row["source_cycle_time"]
        except Exception:  # noqa: BLE001
            raw_value = row[0]
        if can_verify_payload:
            try:
                artifact_path = str(row["artifact_path"] or "")
                metadata_raw = row["artifact_metadata_json"]
            except Exception:  # noqa: BLE001
                artifact_path = str(row[1] or "")
                metadata_raw = row[2]
            try:
                metadata = json.loads(str(metadata_raw or "{}"))
            except (TypeError, ValueError):
                continue
            if not isinstance(metadata, dict):
                continue
            try:
                from src.config import cities_by_name
                from src.data.replacement_forecast_current_target_plan import (
                    _openmeteo_payload_covers_target_local_day,
                )

                city_cfg = cities_by_name.get(str(city))
                city_timezone = str(getattr(city_cfg, "timezone", "") or "") or None
                if not _openmeteo_payload_covers_target_local_day(
                    metadata,
                    artifact_path=artifact_path,
                    city_timezone=city_timezone,
                    target_date=str(target_date),
                ):
                    continue
            except Exception:  # noqa: BLE001 - unverifiable artifact is not executable HWM
                continue
        return _parse_source_cycle_utc(raw_value)
    return None


@lru_cache(maxsize=16)
def _raw_artifact_cycles_for_frozen_target(
    conn: sqlite3.Connection,
    table_ref: str,
    columns: frozenset[str],
    target_date: str,
    metric: str,
    decision_iso: str,
    data_version: int,
    total_changes: int,
) -> tuple[tuple[str, datetime], ...]:
    """Resolve all city HWMs once inside one frozen selection transaction."""

    predicates = [
        "json_extract(artifact_metadata_json, '$.target_date') = ?",
        "json_extract(artifact_metadata_json, '$.metric') = ?",
        "datetime(captured_at) <= datetime(?)",
        "datetime(source_available_at) <= datetime(?)",
    ]
    params: list[object] = [target_date, metric, decision_iso, decision_iso]
    if "source_id" in columns:
        predicates.append("source_id = ?")
        params.append(OPENMETEO_ANCHOR_SOURCE_ID)
    if "product_id" in columns:
        predicates.append("product_id = ?")
        params.append(OPENMETEO_ANCHOR_PRODUCT_ID)
    select_path = "artifact_path" if "artifact_path" in columns else "NULL"
    rows = conn.execute(
        f"""
        SELECT json_extract(artifact_metadata_json, '$.city') AS artifact_city,
               json_extract(artifact_metadata_json, '$.target_date') AS artifact_target_date,
               json_extract(artifact_metadata_json, '$.metric') AS artifact_metric,
               source_cycle_time,
               {select_path} AS artifact_path,
               CASE WHEN json_valid(artifact_metadata_json)
                    THEN json_type(artifact_metadata_json) END AS metadata_type,
               CASE WHEN json_valid(artifact_metadata_json)
                    THEN json_type(artifact_metadata_json, '$.openmeteo_payload_json')
               END AS payload_path_type,
               CASE WHEN json_valid(artifact_metadata_json)
                    THEN json_extract(artifact_metadata_json, '$.openmeteo_payload_json')
               END AS payload_path,
               artifact_metadata_json
          FROM {table_ref}
         WHERE {' AND '.join(predicates)}
           AND datetime(source_cycle_time) <= datetime(?)
         GROUP BY artifact_city, source_cycle_time
         ORDER BY artifact_city, datetime(source_cycle_time) DESC
        """,
        tuple([*params, decision_iso]),
    ).fetchall()

    cycles = _artifact_cycles_from_rows(rows, columns=columns)
    return tuple(
        sorted(
            (city, cycle)
            for (city, row_target, row_metric), cycle in cycles.items()
            if row_target == target_date and row_metric == metric
        )
    )


def _artifact_cycles_from_rows(
    rows: Iterable[sqlite3.Row | tuple[object, ...]],
    *,
    columns: frozenset[str],
    requested_keys: frozenset[tuple[str, str, str]] | None = None,
) -> dict[tuple[str, str, str], datetime]:
    from src.config import cities_by_name
    from src.data.replacement_forecast_current_target_plan import (
        _openmeteo_payload_covers_target_local_day,
    )

    cycles: dict[tuple[str, str, str], datetime] = {}
    for row in rows:
        try:
            artifact_city = str(row["artifact_city"] or "")
            target_date = str(row["artifact_target_date"] or "")
            metric = str(row["artifact_metric"] or "")
            raw_cycle = row["source_cycle_time"]
            artifact_path = str(row["artifact_path"] or "")
            metadata_type = str(row["metadata_type"] or "")
            payload_path_type = str(row["payload_path_type"] or "")
            payload_path = row["payload_path"]
            metadata_raw = row["artifact_metadata_json"]
        except Exception:  # noqa: BLE001 - tuple row compatibility
            artifact_city = str(row[0] or "")
            target_date = str(row[1] or "")
            metric = str(row[2] or "")
            raw_cycle = row[3]
            artifact_path = str(row[4] or "")
            metadata_type = str(row[5] or "")
            payload_path_type = str(row[6] or "")
            payload_path = row[7]
            metadata_raw = row[8]
        key = (artifact_city, target_date, metric)
        if (
            not all(key)
            or key in cycles
            or (requested_keys is not None and key not in requested_keys)
        ):
            continue
        if metadata_type != "object":
            continue
        if "artifact_path" in columns:
            if payload_path_type == "text":
                if not _cached_artifact_payload_covers_target_local_day(
                    artifact_path=artifact_path,
                    payload_path=str(payload_path or ""),
                    city_timezone=str(
                        getattr(cities_by_name.get(artifact_city), "timezone", "")
                        or ""
                    ),
                    target_date=target_date,
                ):
                    continue
                metadata = {}
            elif payload_path_type in {"", "null"}:
                metadata = {}
            else:
                try:
                    metadata = json.loads(str(metadata_raw or "{}"))
                except (TypeError, ValueError):
                    continue
                if not isinstance(metadata, dict):
                    continue
            if payload_path_type != "text":
                city_cfg = cities_by_name.get(artifact_city)
                city_timezone = str(getattr(city_cfg, "timezone", "") or "") or None
                if not _openmeteo_payload_covers_target_local_day(
                    metadata,
                    artifact_path=artifact_path,
                    city_timezone=city_timezone,
                    target_date=target_date,
                ):
                    continue
        cycle = _parse_source_cycle_utc(raw_cycle)
        if cycle is not None:
            cycles[key] = cycle
    return cycles


@lru_cache(maxsize=4096)
def _cached_artifact_payload_coverage(
    *,
    artifact_path: str,
    payload_path: str,
    city_timezone: str,
    target_date: str,
    payload_inode: int,
    payload_ctime_ns: int,
    payload_mtime_ns: int,
    payload_size: int,
) -> bool:
    """Verify one immutable payload identity once per process."""

    del payload_inode, payload_ctime_ns, payload_mtime_ns, payload_size
    from src.data.replacement_forecast_current_target_plan import (
        _openmeteo_payload_covers_target_local_day,
    )

    return _openmeteo_payload_covers_target_local_day(
        {"openmeteo_payload_json": payload_path},
        artifact_path=artifact_path,
        city_timezone=city_timezone or None,
        target_date=target_date,
    )


def _cached_artifact_payload_covers_target_local_day(
    *,
    artifact_path: str,
    payload_path: str,
    city_timezone: str,
    target_date: str,
) -> bool:
    """Reuse coverage proof while the referenced payload bytes are unchanged."""

    if not str(payload_path).strip():
        return True
    resolved = Path(payload_path)
    if not resolved.is_absolute():
        resolved = Path(artifact_path).parent / resolved
    _note_file(resolved)
    try:
        stat = resolved.stat()
    except (OSError, ValueError):
        payload_inode = -1
        payload_ctime_ns = -1
        payload_mtime_ns = -1
        payload_size = -1
    else:
        payload_inode = int(stat.st_ino)
        payload_ctime_ns = int(stat.st_ctime_ns)
        payload_mtime_ns = int(stat.st_mtime_ns)
        payload_size = int(stat.st_size)
    return _cached_artifact_payload_coverage(
        artifact_path=artifact_path,
        payload_path=str(resolved),
        city_timezone=city_timezone,
        target_date=target_date,
        payload_inode=payload_inode,
        payload_ctime_ns=payload_ctime_ns,
        payload_mtime_ns=payload_mtime_ns,
        payload_size=payload_size,
    )


def _batch_product_cycle_artifact_cycles(
    conn: sqlite3.Connection,
    *,
    table_ref: str,
    columns: frozenset[str],
    requests: frozenset[tuple[str, str, str]],
    decision_iso: str,
    deadline_monotonic: float | None = None,
    sql_timeout_seconds: float | None = None,
) -> dict[tuple[str, str, str], datetime]:
    """Resolve each requested family through the product-family cycle index."""

    select_path = "artifact.artifact_path" if "artifact_path" in columns else "NULL"
    cycles: dict[tuple[str, str, str], datetime] = {}
    for requested_key in sorted(requests):
        if requested_key in cycles:
            continue
        city, target_date, metric = requested_key
        cursor: sqlite3.Cursor | None = None
        params = (
            OPENMETEO_ANCHOR_SOURCE_ID,
            OPENMETEO_ANCHOR_PRODUCT_ID,
            city,
            target_date,
            metric,
            decision_iso,
            decision_iso,
            decision_iso,
            decision_iso,
        )
        query = f"""
            SELECT CASE WHEN json_valid(artifact_metadata_json)
                    THEN json_extract(artifact_metadata_json, '$.city')
               END AS artifact_city,
               CASE WHEN json_valid(artifact_metadata_json)
                    THEN json_extract(artifact_metadata_json, '$.target_date')
               END AS artifact_target_date,
               CASE WHEN json_valid(artifact_metadata_json)
                    THEN json_extract(artifact_metadata_json, '$.metric')
               END AS artifact_metric,
               source_cycle_time,
               {select_path} AS artifact_path,
               CASE WHEN json_valid(artifact_metadata_json)
                    THEN json_type(artifact_metadata_json)
               END AS metadata_type,
               CASE WHEN json_valid(artifact_metadata_json)
                    THEN json_type(
                        artifact_metadata_json,
                        '$.openmeteo_payload_json'
                    )
               END AS payload_path_type,
               CASE WHEN json_valid(artifact_metadata_json)
                    THEN json_extract(
                        artifact_metadata_json,
                        '$.openmeteo_payload_json'
                    )
               END AS payload_path,
               artifact_metadata_json
          FROM {table_ref} AS artifact
         WHERE artifact.source_id = ?
           AND artifact.product_id = ?
           AND (CASE WHEN json_valid(artifact.artifact_metadata_json)
                     THEN CAST(json_extract(artifact.artifact_metadata_json, '$.city') AS TEXT)
                END) = ?
           AND (CASE WHEN json_valid(artifact.artifact_metadata_json)
                     THEN CAST(json_extract(artifact.artifact_metadata_json, '$.target_date') AS TEXT)
                END) = ?
           AND (CASE WHEN json_valid(artifact.artifact_metadata_json)
                     THEN CAST(json_extract(artifact.artifact_metadata_json, '$.metric') AS TEXT)
                END) = ?
           AND artifact.source_cycle_time <= ?
           AND datetime(source_cycle_time) <= datetime(?)
           AND datetime(captured_at) <= datetime(?)
           AND datetime(source_available_at) <= datetime(?)
         ORDER BY source_cycle_time DESC,
                  datetime(captured_at) DESC,
                  datetime(source_available_at) DESC
        """
        try:
            with _bounded_hwm_sql(
                conn, deadline_monotonic, sql_timeout_seconds
            ) as sql_scope:
                cursor = conn.execute(query, params)
                while requested_key not in cycles:
                    row = cursor.fetchone()
                    if row is None:
                        break
                    sql_scope.suspend()
                    try:
                        cycles.update(
                            _artifact_cycles_from_rows(
                                (row,),
                                columns=columns,
                                requested_keys=frozenset((requested_key,)),
                            )
                        )
                    finally:
                        sql_scope.resume()
                    _require_hwm_deadline(
                        deadline_monotonic,
                        basis="raw_artifact_input_hwm_payload_validation_deadline",
                    )
        finally:
            if cursor is not None:
                cursor.close()
        if len(cycles) == len(requests):
            break
    return cycles


@lru_cache(maxsize=64)
def _raw_artifact_cycle_for_frozen_request(
    conn: sqlite3.Connection,
    table_ref: str,
    columns: frozenset[str],
    request: tuple[str, str, str],
    decision_iso: str,
    data_version: int,
    total_changes: int,
) -> datetime | None:
    """Resolve one frozen request through indexed product-cycle partitions."""

    del data_version, total_changes  # cache-key invalidators
    return _batch_product_cycle_artifact_cycles(
        conn,
        table_ref=table_ref,
        columns=columns,
        requests=frozenset((request,)),
        decision_iso=decision_iso,
    ).get(request)


def _batch_artifact_cycles(
    conn: sqlite3.Connection,
    *,
    requests: frozenset[tuple[str, str, str]],
    decision_iso: str,
    deadline_monotonic: float | None = None,
    sql_timeout_seconds: float | None = None,
) -> tuple[bool, dict[tuple[str, str, str], datetime]]:
    table_ref = _bounded_artifact_table_ref(
        conn,
        deadline_monotonic=deadline_monotonic,
        sql_timeout_seconds=sql_timeout_seconds,
    )
    if table_ref is None:
        return True, {}
    columns = _bounded_hwm_table_ref_columns(
        conn,
        table_ref,
        deadline_monotonic=deadline_monotonic,
        sql_timeout_seconds=sql_timeout_seconds,
    )
    required = {
        "source_cycle_time",
        "captured_at",
        "source_available_at",
        "artifact_metadata_json",
    }
    if not required.issubset(columns):
        return True, {}
    if {"source_id", "product_id"}.issubset(columns):
        return True, _batch_product_cycle_artifact_cycles(
            conn,
            table_ref=table_ref,
            columns=columns,
            requests=requests,
            decision_iso=decision_iso,
            deadline_monotonic=deadline_monotonic,
            sql_timeout_seconds=sql_timeout_seconds,
        )
    select_path = "artifact.artifact_path" if "artifact_path" in columns else "NULL"
    source_predicate = (
        "artifact.source_id = 'openmeteo_ecmwf_ifs_9km'"
        if "source_id" in columns
        else "1 = 1"
    )
    cycles: dict[tuple[str, str, str], datetime] = {}
    limit = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    chunk_size = max(1, (limit - 3) // 3)
    ordered = sorted(requests)
    for offset in range(0, len(ordered), chunk_size):
        chunk = ordered[offset : offset + chunk_size]
        values_sql = ",".join("(?,?,?)" for _ in chunk)
        with _bounded_hwm_sql(conn, deadline_monotonic, sql_timeout_seconds):
            rows = conn.execute(
                f"""
                WITH requested(city, target_date, metric) AS (VALUES {values_sql})
            SELECT requested.city AS artifact_city,
                   requested.target_date AS artifact_target_date,
                   requested.metric AS artifact_metric,
                   artifact.source_cycle_time,
                   {select_path} AS artifact_path,
                   CASE WHEN json_valid(artifact.artifact_metadata_json)
                        THEN json_type(artifact.artifact_metadata_json)
                   END AS metadata_type,
                   CASE WHEN json_valid(artifact.artifact_metadata_json)
                        THEN json_type(
                            artifact.artifact_metadata_json,
                            '$.openmeteo_payload_json'
                        )
                   END AS payload_path_type,
                   CASE WHEN json_valid(artifact.artifact_metadata_json)
                        THEN json_extract(
                            artifact.artifact_metadata_json,
                            '$.openmeteo_payload_json'
                        )
                   END AS payload_path,
                   artifact.artifact_metadata_json
              FROM {table_ref} AS artifact
              JOIN requested
                ON json_extract(
                    artifact.artifact_metadata_json, '$.city'
                ) = requested.city
               AND json_extract(
                    artifact.artifact_metadata_json, '$.target_date'
                ) = requested.target_date
               AND json_extract(
                    artifact.artifact_metadata_json, '$.metric'
                ) = requested.metric
             WHERE {source_predicate}
               AND datetime(artifact.captured_at) <= datetime(?)
               AND datetime(artifact.source_available_at) <= datetime(?)
               AND datetime(artifact.source_cycle_time) <= datetime(?)
             GROUP BY requested.city, requested.target_date, requested.metric,
                      artifact.source_cycle_time
                 ORDER BY requested.city, requested.target_date, requested.metric,
                          datetime(artifact.source_cycle_time) DESC
                """,
                (
                    *[value for key in chunk for value in key],
                    decision_iso,
                    decision_iso,
                    decision_iso,
                ),
            ).fetchall()
        cycles.update(_artifact_cycles_from_rows(rows, columns=columns))
        _require_hwm_deadline(
            deadline_monotonic,
            basis="raw_artifact_input_hwm_payload_validation_deadline",
        )
    return True, cycles


def freeze_replacement_artifact_hwm(
    conn: sqlite3.Connection,
    *,
    requests: Iterable[tuple[str, str, str]],
    decision_time: datetime,
    deadline_monotonic: float | None = None,
    sql_timeout_seconds: float | None = None,
) -> _FrozenInputHwm | None:
    """Read one immutable artifact-HWM cut for a set of held families."""

    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        return None
    normalized = frozenset(
        (str(city), str(target_date), str(metric))
        for city, target_date, metric in requests
        if city and target_date and metric
    )
    if not normalized:
        return None
    decision_iso = decision_time.astimezone(UTC).isoformat()
    artifact_loaded = False
    artifact_cycles: dict[tuple[str, str, str], datetime] = {}
    try:
        artifact_loaded, artifact_cycles = _batch_artifact_cycles(
            conn,
            requests=normalized,
            decision_iso=decision_iso,
            deadline_monotonic=deadline_monotonic,
            sql_timeout_seconds=sql_timeout_seconds,
        )
        _require_hwm_deadline(
            deadline_monotonic,
            basis="raw_artifact_input_hwm_payload_validation_deadline",
        )
    except ReplacementInputHwmReadUnavailable:
        raise
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="raw_artifact_input_hwm_read_unavailable",
        )

    return _FrozenInputHwm(
        conn=None,
        decision_iso=decision_iso,
        requests=normalized,
        artifact_loaded=artifact_loaded,
        artifact_cycles=MappingProxyType(dict(artifact_cycles)),
    )


def frozen_replacement_artifact_hwm_unavailable(
    *,
    requests: Iterable[tuple[str, str, str]],
    decision_time: datetime,
    blocker_reason: str,
) -> _FrozenInputHwm | None:
    """Build one cycle-scoped UNKNOWN verdict after a failed batch read."""

    normalized = frozenset(
        (str(city), str(target_date), str(metric))
        for city, target_date, metric in requests
        if city and target_date and metric
    )
    if not normalized:
        return None
    return _FrozenInputHwm(
        conn=None,
        decision_iso=decision_time.astimezone(UTC).isoformat(),
        requests=normalized,
        artifact_loaded=False,
        artifact_cycles=MappingProxyType({}),
        blocker_reason=str(blocker_reason or "batch read unavailable"),
    )


def install_frozen_replacement_artifact_hwm(
    snapshot: _FrozenInputHwm | None,
) -> Callable[[], None]:
    """Install an immutable HWM cut for one synchronous consumer call."""

    if snapshot is None:
        return lambda: None
    token = _FROZEN_INPUT_HWM.set(snapshot)
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        _FROZEN_INPUT_HWM.reset(token)

    return release


def prime_frozen_replacement_artifact_hwm(
    conn: sqlite3.Connection,
    *,
    requests: Iterable[tuple[str, str, str]],
    decision_time: datetime,
) -> Callable[[], None]:
    """Prime artifact HWMs for one explicitly owned read transaction."""

    snapshot = freeze_replacement_artifact_hwm(
        conn,
        requests=requests,
        decision_time=decision_time,
    )
    if snapshot is not None:
        snapshot = _FrozenInputHwm(
            conn=conn,
            decision_iso=snapshot.decision_iso,
            requests=snapshot.requests,
            artifact_loaded=snapshot.artifact_loaded,
            artifact_cycles=snapshot.artifact_cycles,
            blocker_reason=snapshot.blocker_reason,
        )
    return install_frozen_replacement_artifact_hwm(snapshot)


def _posterior_provenance_for_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    posterior_source_cycle_time: object,
    posterior_computed_at: object | None = None,
) -> dict[str, object] | None:
    table_ref = _authority_table_ref(conn, "forecast_posteriors")
    if table_ref is None:
        return None
    columns = _hwm_table_ref_columns(conn, table_ref)
    required = {"city", "target_date", "temperature_metric", "source_cycle_time", "provenance_json"}
    if not required.issubset(columns):
        return None
    parsed_computed_at = _parse_source_cycle_utc(posterior_computed_at)
    if posterior_computed_at not in (None, "") and parsed_computed_at is None:
        return None
    exact_computed_at = (
        parsed_computed_at.isoformat() if parsed_computed_at is not None else None
    )
    if exact_computed_at is not None and "computed_at" not in columns:
        return None
    order_terms = []
    if "computed_at" in columns:
        order_terms.append("datetime(computed_at) DESC")
    if "posterior_id" in columns:
        order_terms.append("posterior_id DESC")
    order_sql = ", ".join(order_terms) if order_terms else "rowid DESC"
    try:
        rows = conn.execute(
            f"""
            SELECT provenance_json, computed_at
              FROM {table_ref}
             WHERE city = ?
               AND target_date = ?
               AND temperature_metric = ?
               AND datetime(source_cycle_time) = datetime(?)
             ORDER BY {order_sql}
            """,
            (city, target_date, metric, str(posterior_source_cycle_time)),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="posterior_provenance_hwm_read_unavailable",
        )
    if not rows:
        return None
    if exact_computed_at is not None:
        exact_rows = []
        for candidate in rows:
            try:
                candidate_computed_at = candidate["computed_at"]
            except Exception:  # noqa: BLE001 - tuple row compatibility
                candidate_computed_at = candidate[1]
            if _parse_source_cycle_utc(candidate_computed_at) == parsed_computed_at:
                exact_rows.append(candidate)
        if len(exact_rows) != 1:
            return None
        row = exact_rows[0]
    else:
        row = rows[0]
    try:
        raw = row["provenance_json"]
    except Exception:  # noqa: BLE001
        raw = row[0]
    try:
        provenance = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return None
    return provenance if isinstance(provenance, dict) else None


def _posterior_used_models_for_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    posterior_source_cycle_time: object,
) -> frozenset[str]:
    provenance = _posterior_provenance_for_cycle(
        conn,
        city=city,
        target_date=target_date,
        metric=metric,
        posterior_source_cycle_time=posterior_source_cycle_time,
    )
    if not provenance:
        return frozenset()

    return _used_models_from_provenance(provenance)


def _used_models_from_provenance(
    provenance: Mapping[str, object],
) -> frozenset[str]:
    fusion = provenance.get("bayes_precision_fusion")
    candidates: list[object] = []
    if isinstance(fusion, dict):
        source_clock = fusion.get("source_clock_one_scheme")
        if isinstance(source_clock, dict):
            candidates.append(source_clock.get("used_weights"))
        candidates.append(fusion.get("used_models"))
    candidates.append(provenance.get("used_models"))
    models: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, dict):
            values = candidate.keys()
        elif isinstance(candidate, (list, tuple, set)):
            values = candidate
        else:
            continue
        for value in values:
            text = str(value or "").strip()
            if text:
                models.add(text)
        if models:
            break
    return frozenset(models)


def _provenance_has_current_value_serving(
    provenance: Mapping[str, object],
) -> bool:
    fusion = provenance.get("bayes_precision_fusion")
    if not isinstance(fusion, dict):
        return False
    serving = fusion.get("current_value_serving")
    return isinstance(serving, dict) and bool(serving)


def _current_station_ground_state(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    decision_time: datetime,
) -> tuple[str, str, str] | None:
    """(facts identity, coverage status, applicability) of the current ground.

    Read on every call: it is an input of the remembered verdict, never part
    of the remembered outcome. Unavailable ground is None.
    """
    from src.data.replacement_current_value_serving import station_ground_target_coverage_for_city
    from src.data.station_ground_evidence import (
        forecast_db_from_connection, read_current_station_ground_evidence,
    )

    db_path = forecast_db_from_connection(conn)
    if db_path is None:
        return None
    current = read_current_station_ground_evidence(db_path, city=city, decision_at=decision_time)
    if current is None:
        return None
    coverage = station_ground_target_coverage_for_city(
        current, city=city, target_date=target_date, decision_at=decision_time,
    )
    return (str(current["facts_identity"]), str(coverage["status"]),
            str(coverage["applicability_identity"]))


def _recorded_serving_claims(
    fusion: Mapping[str, object],
) -> dict[int, tuple[str, datetime, datetime | None, object, str]] | None:
    """Every raw row a posterior records as consumed, by id, across its roles.

    The roles are read from the provenance itself: ``current_value_serving``
    and every ``*_value_serving`` mapping in ``source_clock_one_scheme``. One
    row shared by several roles must claim one identity; a malformed or
    conflicting claim returns None (unverifiable).
    """
    from src.data.replacement_current_value_serving import physical_source_proof_dependency

    scheme = fusion.get("source_clock_one_scheme")
    roles = [("current_value_serving", fusion.get("current_value_serving"))]
    if isinstance(scheme, Mapping):
        roles += [(key, value) for key, value in scheme.items()
                  if str(key).endswith("_value_serving")]
    claims: dict[int, tuple[str, datetime, datetime | None, object, str]] = {}
    for role, serving in roles:
        if serving is None and role != "current_value_serving":
            continue
        if not isinstance(serving, Mapping):
            return None
        for model, item in serving.items():
            if not isinstance(item, Mapping):
                return None
            try:
                raw_id = int(item.get("raw_model_forecast_id"))
            except (TypeError, ValueError):
                return None
            cycle = _parse_source_cycle_utc(item.get("served_cycle"))
            if raw_id <= 0 or cycle is None:
                return None
            claim = (str(model), cycle, _parse_source_cycle_utc(item.get("captured_at")),
                     item.get("physical_response"), role)
            prior = claims.setdefault(raw_id, claim)
            if prior[:3] != claim[:3] or (physical_source_proof_dependency(prior[3])
                                          != physical_source_proof_dependency(claim[3])):
                return None
    return claims


def _exact_current_value_serving_lag(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    posterior_computed_at: datetime | None,
    provenance: Mapping[str, object],
    held_complete_bundle_continuity: bool = False,
    refresh_reasons: list[str] | None = None,
    input_witness_out: dict[str, object] | None = None,
    consumed_proof_verified: bool = False,
) -> tuple[bool, str | None, datetime | None]:
    """Validate consumed proof, then separately record each model's refresh debt.

    ``forecast_posteriors.source_cycle_time`` is the carrier/shape cycle.  A
    source-clock posterior may intentionally consume newer, model-specific
    deterministic values, recorded in ``current_value_serving``.  Comparing
    those rows back to the carrier cycle makes a fully current posterior look
    stale forever.  Exact raw-row identities are the narrower authority.

    The successor census (newer rows per consumed model) runs only when a
    caller collects ``refresh_reasons`` or a witness: it never decides serving.
    """

    census = refresh_reasons is not None or input_witness_out is not None
    refresh_reasons = [] if refresh_reasons is None else refresh_reasons
    fusion = provenance.get("bayes_precision_fusion")
    if not isinstance(fusion, Mapping):
        return False, None, None
    serving = fusion.get("current_value_serving")
    used_models = _used_models_from_provenance(provenance)
    if not isinstance(serving, Mapping) or not used_models:
        return True, "basis=current_value_serving_provenance_unverifiable", None

    from src.data.replacement_current_value_serving import day0_remaining_from_provenance
    day0_tau, window_reason = day0_remaining_from_provenance(
        provenance, city=city, target_date=target_date, metric=metric,
        posterior_computed_at=posterior_computed_at,
    )
    if window_reason is not None:
        return True, window_reason, None

    shape = fusion.get("current_evidence_shape")
    if (not consumed_proof_verified and isinstance(shape, Mapping)
            and isinstance(shape.get("provider_geometry_evidence"), Mapping)):
        from src.data.station_ground_evidence import read_frozen_station_ground_evidence
        ground_audit = shape.get("provider_geometry_audit")
        frozen = None if posterior_computed_at is None else read_frozen_station_ground_evidence(
            ground_audit.get("anchor_station_ground") if isinstance(ground_audit, Mapping) else None,
            decision_at=posterior_computed_at,
        )
        current_ground = _current_station_ground_state(
            conn, city=city, target_date=target_date, decision_time=decision_time,
        )
        if frozen is None or current_ground is None:
            return True, "basis=station_ground_canonical_evidence_unavailable", None
        facts_identity, coverage_status, applicability = current_ground
        if frozen["facts_identity"] != facts_identity:
            return True, "basis=station_ground_current_facts_changed", None
        from src.data.replacement_current_value_serving import station_ground_target_coverage_for_city
        prior_coverage = station_ground_target_coverage_for_city(frozen,city=city,target_date=target_date,
            decision_at=posterior_computed_at)
        if (coverage_status != "VERIFIED"
            or prior_coverage["applicability_identity"] != applicability):
            return True,"basis=station_ground_target_applicability_changed",None
        # Whole-page/manifest/possession changes with the exact same station
        # facts never invalidate a certificate or force a new probability shape.

    consumed: dict[str, tuple[int, datetime, datetime | None]] = {}
    for model in sorted(used_models):
        item = serving.get(model)
        if model == "ecmwf_ifs" and item is None:
            if consumed_proof_verified:
                continue
            from src.data.replacement_forecast_cycle_policy import current_evidence_shape_has_held_authority
            from src.data.station_ground_evidence import forecast_db_from_connection
            table = _authority_table_ref(conn, "forecast_posteriors")
            anchor_id = None
            if (table is not None and posterior_computed_at is not None
                and "openmeteo_anchor_id" in _hwm_table_ref_columns(conn, table)):
                try:
                    candidates = conn.execute(f"SELECT computed_at,provenance_json,openmeteo_anchor_id FROM {table}"
                        " WHERE city=? AND target_date=? AND temperature_metric=? AND datetime(computed_at)=datetime(?)",
                        (city,str(target_date),metric,posterior_computed_at.isoformat())).fetchall()
                except sqlite3.OperationalError as exc:
                    _raise_hwm_read_unavailable(exc, basis="posterior_anchor_namespace_read_unavailable")
                matching = []
                for candidate in candidates:
                    if _parse_source_cycle_utc(candidate[0]) != posterior_computed_at:
                        continue
                    try:
                        actual_provenance = json.loads(str(candidate[1]))
                    except (TypeError, ValueError):
                        continue
                    if actual_provenance == provenance:
                        matching.append(candidate[2])
                if len(matching) == 1:
                    anchor_id = matching[0]
            audit = shape.get("provider_geometry_audit") if isinstance(shape,Mapping) else None
            if (not isinstance(audit,Mapping) or audit.get("anchor_ifs9_role") != "anchor_only"
                or not current_evidence_shape_has_held_authority(provenance,materialized_at=posterior_computed_at,
                    city=city,target_date=target_date,metric=metric,anchor_id=anchor_id,
                    forecast_db=forecast_db_from_connection(conn))):
                return True,"basis=anchor_only_ifs9_provenance_unverifiable",None
            continue
        if not isinstance(item, Mapping):
            return (
                True,
                f"basis=current_value_serving_provenance_unverifiable:model={model}",
                None,
            )
        try:
            raw_id = int(item.get("raw_model_forecast_id"))
        except (TypeError, ValueError):
            return (
                True,
                f"basis=current_value_serving_provenance_unverifiable:model={model}",
                None,
            )
        served_cycle = _parse_source_cycle_utc(item.get("served_cycle"))
        if raw_id <= 0 or served_cycle is None:
            return (
                True,
                f"basis=current_value_serving_provenance_unverifiable:model={model}",
                None,
            )
        consumed[model] = (
            raw_id,
            served_cycle,
            _parse_source_cycle_utc(item.get("captured_at")),
        )
        if (
            posterior_computed_at is not None
            and consumed[model][2] is not None
            and consumed[model][2] > posterior_computed_at
        ):
            return (
                True,
                "basis=current_value_serving_consumed_input_after_posterior:"
                f"model={model}:"
                f"consumed_raw_id={raw_id}:"
                f"latest_raw_input_at={consumed[model][2].isoformat()}:"
                f"posterior_computed_at={posterior_computed_at.isoformat()}",
                consumed.get("ecmwf_ifs", (0, served_cycle, None))[1],
            )

    # Validate every consumed identity before inspecting successors. A newer
    # model/body/receipt is not evidence that the consumed one was invalid.
    if posterior_computed_at is None:
        return True, "basis=posterior_computed_at_unverifiable", None
    from src.data.replacement_current_value_serving import (
        read_consumed_instrument_values, physical_source_proof_dependency,
    )
    # The consumed rows are re-proven as consumed: at the posterior's own cut
    # and, for a post-day family, at the Day0 tau recorded with them
    # (day0_remaining_from_provenance), never at a tau derived from the clock
    # or a newer observation. Every recorded serving role is a consumed input:
    # the center rows and the source-clock cohort rows behind the between-
    # provider spread are checked alike.
    claims = {} if consumed_proof_verified else _recorded_serving_claims(fusion)
    if claims is None:
        return True, "basis=current_value_serving_provenance_unverifiable:role_claim", None
    try:
        frozen = read_consumed_instrument_values(
            conn, city=city, metric=metric, target_date=str(target_date),
            consumed_models={raw_id: claim[0] for raw_id, claim in claims.items()},
            materialized_at_iso=posterior_computed_at.isoformat(),
            day0_remaining_from_iso=day0_tau,
        ) if claims else {}
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(exc, basis="consumed_physical_proof_read_unavailable")
    for raw_id, (model, cycle, captured, response, role) in claims.items():
        old = frozen.get(raw_id)
        if old is None:
            return True, ("basis=current_value_serving_consumed_proof_unverifiable:"
                f"model={model}:consumed_raw_id={raw_id}:role={role}"), None
        if (_parse_source_cycle_utc(old.served_cycle) != cycle
            or captured is None or _parse_source_cycle_utc(old.captured_at) != captured):
            return True, ("basis=current_value_serving_raw_row_identity_mismatch:"
                f"model={model}:consumed_raw_id={raw_id}:role={role}"), None
        claimed = physical_source_proof_dependency(response)
        if claimed is None or claimed != physical_source_proof_dependency(old.physical_response):
            return True, ("basis=current_value_serving_consumed_physical_proof_invalid:"
                f"model={model}:consumed_raw_id={raw_id}:role={role}"), None
    anchor = consumed.get("ecmwf_ifs")
    if not census:
        return True, None, anchor[1] if anchor is not None else None

    decision_iso = decision_time.astimezone(UTC).isoformat()
    from src.data.replacement_current_value_serving import (
        read_current_instrument_values,
    )

    try:
        selected = read_current_instrument_values(
            conn,
            city=city,
            metric=metric,
            target_date=str(target_date),
            source_cycle_time_iso=max(
                item[1] for item in consumed.values()
            ).isoformat(),
            include_station_sources=True,
            decision_time_iso=decision_iso,
            day0_remaining_from_iso=day0_tau,
        )
    except sqlite3.OperationalError as exc:
        # The exact consumed proof was checked above. An unavailable successor
        # census is UNKNOWN refresh debt, not UNKNOWN consumed authority.
        refresh_reasons.append("basis=successor_current_value_read_unavailable:"
                               + type(exc).__name__)
        selected = {}
    newer_cycle_changes: list[
        tuple[str, int, int, datetime, datetime]
    ] = []
    if "ecmwf_ifs" in used_models and "ecmwf_ifs" not in serving and "ecmwf_ifs" in selected:
        refresh_reasons.append("basis=anchor_only_ifs9_raw_instrument_became_available")
    for model, (consumed_id, consumed_cycle, consumed_at) in consumed.items():
        current = selected.get(model)
        if input_witness_out is not None:
            current_cycle = _parse_source_cycle_utc(current.served_cycle) if current else None
            current_at = _parse_source_cycle_utc(current.captured_at) if current else None
            input_witness_out.setdefault("used_model_frontier", {})[model] = {
                "consumed_raw_id": consumed_id, "consumed_cycle": consumed_cycle.isoformat(),
                "latest_raw_id": current.raw_model_forecast_id if current else None,
                "latest_cycle": current_cycle.isoformat() if current_cycle else None,
                "cycle_lag_hours": max(0.0,(current_cycle-consumed_cycle).total_seconds()/3600.0) if current_cycle else None,
                "same_cycle_receipt_lag_seconds": max(0.0,(current_at-posterior_computed_at).total_seconds()) if current_at else None,
            }
        if current is None:
            refresh_reasons.append(
                "basis=current_value_serving_successor_unavailable:"
                f"model={model}:consumed_raw_id={consumed_id}")
            continue
        current_cycle = _parse_source_cycle_utc(current.served_cycle)
        from src.data.replacement_current_value_serving import physical_source_proof_dependency
        consumed_proof = serving[model].get("physical_response")
        if physical_source_proof_dependency(consumed_proof) != physical_source_proof_dependency(current.physical_response):
            refresh_reasons.append(
                f"basis=current_value_serving_physical_proof_dependency_changed:model={model}:"
                f"consumed_raw_id={consumed_id}:latest_raw_id={current.raw_model_forecast_id}")
        current_at = _parse_source_cycle_utc(current.captured_at)
        latest_id = int(current.raw_model_forecast_id)
        if current_cycle is None:
            refresh_reasons.append(
                "basis=current_value_serving_successor_identity_unverifiable:"
                f"model={model}:consumed_raw_id={consumed_id}")
            continue
        if (
            posterior_computed_at is not None
            and current_at is not None
            and current_at > posterior_computed_at
            and current_cycle == consumed_cycle
        ):
            refresh_reasons.append(
                "basis=used_raw_model_forecasts_same_cycle_late_input:"
                f"model={model}:latest_raw_id={latest_id}:"
                f"latest_raw_input_at={current_at.isoformat()}:"
                f"posterior_computed_at={posterior_computed_at.isoformat()}")
            continue
        if latest_id == consumed_id:
            # The exact consumed row already re-verified at its cutoff above;
            # a census that sees it under other clocks is successor evidence.
            if (
                current_cycle != consumed_cycle
                or (
                    consumed_at is not None
                    and current_at != consumed_at
                )
            ):
                refresh_reasons.append(
                    "basis=current_value_serving_successor_identity_unverifiable:"
                    f"model={model}:consumed_raw_id={consumed_id}")
            continue
        if current_cycle > consumed_cycle:
            newer_cycle_changes.append(
                (model, latest_id, consumed_id, current_cycle, consumed_cycle)
            )
            continue
        newer_cycle_changes.append(
            (model, latest_id, consumed_id, current_cycle, consumed_cycle))

    for model, latest_id, consumed_id, current_cycle, consumed_cycle in newer_cycle_changes:
        refresh_reasons.append(
            "basis=used_raw_model_forecasts_superseded:"
            f"model={model}:latest_raw_id={latest_id}:consumed_raw_id={consumed_id}:"
            f"latest_raw_cycle={current_cycle.isoformat()}:"
            f"consumed_raw_cycle={consumed_cycle.isoformat()}")

    return True, None, anchor[1] if anchor is not None else None


def _exact_consumed_anchor_artifact_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    provenance: Mapping[str, object],
    posterior_computed_at: datetime | None = None,
) -> tuple[str | None, datetime | None]:
    """Return the exact OpenMeteo artifact cycle consumed by a posterior.

    ``current_value_serving.ecmwf_ifs`` identifies the deterministic model row,
    not the OpenMeteo anchor artifact.  The two clocks may legitimately straddle
    a UTC cycle boundary.  HWM comparison therefore binds to the immutable
    artifact id persisted by the materializer and rejects any unverifiable
    identity instead of substituting a nearby model clock.
    """

    try:
        artifact_id = int(provenance.get("openmeteo_anchor_artifact_id"))
    except (TypeError, ValueError):
        return "basis=openmeteo_anchor_artifact_provenance_unverifiable", None
    if artifact_id <= 0:
        return "basis=openmeteo_anchor_artifact_provenance_unverifiable", None

    table_ref = _authority_table_ref(conn, "raw_forecast_artifacts")
    if table_ref is None:
        return "basis=openmeteo_anchor_artifact_table_unavailable", None
    columns = _hwm_table_ref_columns(conn, table_ref)
    required = {
        "artifact_id",
        "source_id",
        "product_id",
        "data_version",
        "source_cycle_time",
        "source_available_at",
        "captured_at",
        "artifact_path",
        "sha256",
        "artifact_metadata_json",
    }
    if not required.issubset(columns):
        return "basis=openmeteo_anchor_artifact_table_unverifiable", None

    try:
        row = conn.execute(
            f"""
            SELECT artifact_id, source_id, product_id, data_version,
                   source_cycle_time, source_available_at, captured_at,
                   artifact_path, sha256, artifact_metadata_json
              FROM {table_ref}
             WHERE artifact_id = ?
             LIMIT 1
            """,
            (artifact_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="anchor_artifact_hwm_read_unavailable",
        )
    if row is None:
        return (
            f"basis=openmeteo_anchor_artifact_missing:artifact_id={artifact_id}",
            None,
        )
    values = dict(row) if hasattr(row, "keys") else dict(
        zip(
            (
                "artifact_id",
                "source_id",
                "product_id",
                "data_version",
                "source_cycle_time",
                "source_available_at",
                "captured_at",
                "artifact_path",
                "sha256",
                "artifact_metadata_json",
            ),
            row,
            strict=True,
        )
    )
    normalized_metric = str(metric).strip().lower()
    if (
        str(values["source_id"]) != OPENMETEO_ANCHOR_SOURCE_ID
        or str(values["product_id"]) != OPENMETEO_ANCHOR_PRODUCT_ID
        or str(values["data_version"])
        != f"openmeteo_ecmwf_ifs9_anchor_localday_{normalized_metric}"
    ):
        return (
            "basis=openmeteo_anchor_artifact_identity_mismatch:"
            f"artifact_id={artifact_id}",
            None,
        )

    source_cycle = _parse_source_cycle_utc(values["source_cycle_time"])
    source_available_at = _parse_source_cycle_utc(values["source_available_at"])
    captured_at = _parse_source_cycle_utc(values["captured_at"])
    decision_utc = decision_time.astimezone(UTC)
    if (
        source_cycle is None
        or source_available_at is None
        or captured_at is None
        or source_cycle > decision_utc
        or source_available_at > decision_utc
        or captured_at > decision_utc
    ):
        return (
            "basis=openmeteo_anchor_artifact_causality_mismatch:"
            f"artifact_id={artifact_id}",
            None,
        )

    artifact_path = Path(str(values["artifact_path"] or ""))
    fusion = provenance.get("bayes_precision_fusion")
    shape = fusion.get("current_evidence_shape") if isinstance(fusion,Mapping) else None
    audit = shape.get("provider_geometry_audit") if isinstance(shape,Mapping) else None
    claimed_local_proof = audit.get("anchor_local_proof") if isinstance(audit,Mapping) else None
    if claimed_local_proof is not None:
        from src.data.raw_forecast_artifact_manifest import read_anchor_local_proof
        from src.data.replacement_forecast_cycle_policy import anchor_local_proof_dependency
        from src.data.station_ground_evidence import forecast_db_from_connection
        try:
            if posterior_computed_at is None:
                return "basis=anchor_local_proof_cut_unavailable",None
            local = read_anchor_local_proof(conn,artifact_id,city=city,target_date=str(target_date),
                metric=normalized_metric,decision_at=posterior_computed_at)
            if local is None or claimed_local_proof != anchor_local_proof_dependency(local,forecast_db=forecast_db_from_connection(conn)):
                return "basis=anchor_local_proof_identity_unverifiable",None
            artifact_path = Path(str(local.owned_body["path"]))
        except (ValueError,OSError):
            return "basis=anchor_local_proof_identity_unverifiable",None
    expected_sha = str(values["sha256"] or "").strip().lower()
    try:
        actual_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    except OSError:
        return (
            "basis=openmeteo_anchor_artifact_payload_unavailable:"
            f"artifact_id={artifact_id}",
            None,
        )
    if actual_sha != expected_sha:
        return (
            "basis=openmeteo_anchor_artifact_payload_identity_mismatch:"
            f"artifact_id={artifact_id}",
            None,
        )

    try:
        metadata = json.loads(str(values["artifact_metadata_json"] or "{}"))
    except (TypeError, ValueError):
        metadata = None
    if not isinstance(metadata, Mapping):
        return (
            "basis=openmeteo_anchor_artifact_metadata_unverifiable:"
            f"artifact_id={artifact_id}",
            None,
        )
    if claimed_local_proof is not None:
        # A derived read view retains original source identity/clocks. This
        # owned copy is not a rewritten historical path or a fresh issue.
        metadata = {**metadata,"openmeteo_payload_json":str(artifact_path)}
    artifact_row = {
        "artifact_city": metadata.get("city"),
        # One immutable Open-Meteo payload can cover several local days. Bind
        # this HWM proof to the posterior's consumed day; the validator below
        # still checks the original payload bytes, hash, city, metric, and
        # actual local-day coverage.
        "artifact_target_date": str(target_date),
        "artifact_metric": metadata.get("metric"),
        "source_cycle_time": values["source_cycle_time"],
        "artifact_path": str(artifact_path),
        "metadata_type": "object",
        "payload_path_type": (
            "text" if isinstance(metadata.get("openmeteo_payload_json"), str) else ""
        ),
        "payload_path": metadata.get("openmeteo_payload_json"),
        "artifact_metadata_json": json.dumps(metadata),
    }
    key = (str(city), str(target_date), normalized_metric)
    validated_cycle = _artifact_cycles_from_rows(
        (artifact_row,),
        columns=columns,
        requested_keys=frozenset((key,)),
    ).get(key)
    if validated_cycle != source_cycle:
        return (
            "basis=openmeteo_anchor_artifact_scope_mismatch:"
            f"artifact_id={artifact_id}",
            None,
        )
    return None, source_cycle


def latest_used_raw_model_input_mark(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    posterior_source_cycle_time: object,
    posterior_provenance: Mapping[str, object] | None = None,
) -> tuple[datetime, datetime | None] | None:
    """Latest used-model raw cycle plus latest row evidence timestamp."""

    used_models = (
        _used_models_from_provenance(posterior_provenance)
        if posterior_provenance is not None
        else _posterior_used_models_for_cycle(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            posterior_source_cycle_time=posterior_source_cycle_time,
        )
    )
    if not used_models:
        return None
    table_ref = _authority_table_ref(conn, "raw_model_forecasts")
    if table_ref is None:
        return None
    columns = _hwm_table_ref_columns(conn, table_ref)
    required = {"model", "city", "target_date", "metric", "source_cycle_time"}
    if not required.issubset(columns):
        return None
    predicates = ["city = ?", "target_date = ?", "metric = ?"]
    params: list[object] = [city, target_date, metric]
    decision_iso = decision_time.astimezone(UTC).isoformat()
    if "endpoint" in columns:
        predicates.append("endpoint = 'single_runs'")
    if "coverage_status" in columns:
        predicates.append("(coverage_status IS NULL OR coverage_status = 'COVERED')")
    if "captured_at" in columns:
        predicates.append("(captured_at IS NULL OR datetime(captured_at) <= datetime(?))")
        params.append(decision_iso)
    if "source_available_at" in columns:
        predicates.append(
            "(source_available_at IS NULL OR datetime(source_available_at) <= datetime(?))"
        )
        params.append(decision_iso)
    placeholders = ",".join("?" for _ in used_models)
    params.extend(sorted(used_models))
    captured_select = "captured_at" if "captured_at" in columns else "NULL AS captured_at"
    available_select = (
        "source_available_at"
        if "source_available_at" in columns
        else "NULL AS source_available_at"
    )
    evidence_order_terms = ["datetime(source_cycle_time)"]
    if "captured_at" in columns:
        evidence_order_terms.append("COALESCE(datetime(captured_at), '0001-01-01 00:00:00')")
    if "source_available_at" in columns:
        evidence_order_terms.append("COALESCE(datetime(source_available_at), '0001-01-01 00:00:00')")
    evidence_order_sql = "MAX(" + ", ".join(evidence_order_terms) + ")"
    try:
        row = conn.execute(
            f"""
            SELECT source_cycle_time, {captured_select}, {available_select}
              FROM {table_ref}
             WHERE {' AND '.join(predicates)}
               AND model IN ({placeholders})
               AND datetime(source_cycle_time) <= datetime(?)
             ORDER BY datetime(source_cycle_time) DESC, {evidence_order_sql} DESC
             LIMIT 1
            """,
            tuple([*params, decision_iso]),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="used_raw_model_input_hwm_read_unavailable",
        )
    if row is None:
        return None
    try:
        raw_value = row["source_cycle_time"]
        captured_at = row["captured_at"]
        source_available_at = row["source_available_at"]
    except Exception:  # noqa: BLE001
        raw_value = row[0]
        captured_at = row[1] if len(row) > 1 else None
        source_available_at = row[2] if len(row) > 2 else None
    raw_cycle = _parse_source_cycle_utc(raw_value)
    if raw_cycle is None:
        return None
    return raw_cycle, _latest_utc_timestamp(captured_at, source_available_at)


def latest_used_raw_model_input_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    posterior_source_cycle_time: object,
) -> datetime | None:
    mark = latest_used_raw_model_input_mark(
        conn,
        city=city,
        target_date=target_date,
        metric=metric,
        decision_time=decision_time,
        posterior_source_cycle_time=posterior_source_cycle_time,
    )
    return mark[0] if mark is not None else None


def latest_live_input_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
) -> tuple[datetime | None, str | None]:
    candidates = [
        (
            latest_raw_model_input_cycle(
                conn, city=city, target_date=target_date, metric=metric, decision_time=decision_time
            ),
            "source_cycle_time_raw_model_forecasts_lag",
        ),
        (
            latest_raw_artifact_input_cycle(
                conn, city=city, target_date=target_date, metric=metric, decision_time=decision_time
            ),
            "source_cycle_time_raw_forecast_artifacts_lag",
        ),
    ]
    candidates = [(cycle, basis) for cycle, basis in candidates if cycle is not None]
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: item[0])


def _latest_eligible_ensemble_input_mark(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    day0_remaining_from_iso: str | None = None,
) -> tuple[int, datetime] | None:
    """Return the newest decision-time-available full ENS cycle for one family.

    ``day0_remaining_from_iso`` remains a caller-compatible scope argument.
    A partial remaining-window scalar never names a full-target shape, so it
    cannot supersede a lawful full-target certificate or reset held continuity.
    """

    table_ref = _authority_table_ref(conn, "ensemble_snapshots")
    if table_ref is None:
        return None
    columns = _hwm_table_ref_columns(conn, table_ref)
    required = {"snapshot_id", "city", "target_date", "temperature_metric"}
    if not required.issubset(columns):
        return None
    cycle_expr = (
        "COALESCE(source_cycle_time, issue_time)"
        if {"source_cycle_time", "issue_time"}.issubset(columns)
        else "source_cycle_time"
        if "source_cycle_time" in columns
        else "issue_time"
        if "issue_time" in columns
        else None
    )
    available_expr = (
        "COALESCE(source_available_at, available_at)"
        if {"source_available_at", "available_at"}.issubset(columns)
        else "source_available_at"
        if "source_available_at" in columns
        else "available_at"
        if "available_at" in columns
        else None
    )
    if cycle_expr is None or available_expr is None:
        return None
    predicates = [
        "city = ?",
        "target_date = ?",
        "temperature_metric = ?",
        f"datetime({available_expr}) <= datetime(?)",
    ]
    params: list[object] = [
        city,
        str(target_date),
        metric,
        decision_time.astimezone(UTC).isoformat(),
    ]
    if "authority" in columns:
        predicates.append("authority = 'VERIFIED'")
    # Match the materializer's source/product, coordinate and target-window law.
    eligibility_columns = {
        "causality_status",
        "boundary_ambiguous",
        "contributes_to_target_extrema",
        "forecast_window_attribution_status",
    }
    window_law: tuple[tuple[str, str], ...] = ()
    if eligibility_columns.issubset(columns):
        from src.data.forecast_extrema_authority import (  # noqa: PLC0415
            current_evidence_ensemble_eligibility_sql,
        )

        predicates.append(current_evidence_ensemble_eligibility_sql())
    else:
        if "causality_status" in columns:
            predicates.append("causality_status = 'OK'")
        if "boundary_ambiguous" in columns:
            predicates.append("boundary_ambiguous = 0")
        if "contributes_to_target_extrema" in columns:
            predicates.append("COALESCE(contributes_to_target_extrema, 0) = 1")
        window_law = (
            ("forecast_window_attribution_status", "FULLY_INSIDE_TARGET_LOCAL_DAY"),
        )
    for column, expected_value in (
        ("source_id", "ecmwf_open_data"),
        ("model_version", "ecmwf_ens"),
        *window_law,
    ):
        if column in columns:
            predicates.append(f"{column} = ?")
            params.append(expected_value)
    if "dataset_id" in columns:
        from src.data.replacement_forecast_source_run_identity import (  # noqa: PLC0415
            expected_replacement_dependency_identity_by_role,
            register_native_coordinate_compatibility_sql,
        )

        expected_dataset = expected_replacement_dependency_identity_by_role(metric)[
            "baseline_b0"
        ].data_version
        if expected_dataset is None:
            return None
        register_native_coordinate_compatibility_sql(conn)
        predicates.append("native_coordinate_inputs_current(city, temperature_metric, dataset_id) = 1")
    if "source_run_id" in columns:
        source_authority = ensemble_source_authority_predicate(
            conn,
            ensemble_alias="ensemble_snapshot",
            decision_time=decision_time,
        )
        if source_authority is None:
            return None
        source_predicate, source_params = source_authority
        predicates.append(source_predicate)
        params.extend(source_params)
    try:
        row = conn.execute(
            f"""
            SELECT snapshot_id, {cycle_expr} AS source_cycle_time
              FROM {table_ref} AS ensemble_snapshot
             WHERE {' AND '.join(predicates)}
             ORDER BY datetime({cycle_expr}) DESC,
                      datetime({available_expr}) DESC,
                      snapshot_id DESC
             LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        _raise_hwm_read_unavailable(
            exc,
            basis="ensemble_snapshot_hwm_read_unavailable",
        )
    if row is None:
        return None
    try:
        snapshot_id = int(row["snapshot_id"])
        raw_cycle = row["source_cycle_time"]
    except Exception:  # noqa: BLE001 - tuple row compatibility
        snapshot_id = int(row[0])
        raw_cycle = row[1]
    cycle = _parse_source_cycle_utc(raw_cycle)
    return (snapshot_id, cycle) if cycle is not None else None


def latest_eligible_ensemble_input_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    day0_remaining_from_iso: str | None = None,
) -> datetime | None:
    """Newest decision-time-eligible ENS cycle for pre-materialization admission."""

    mark = _latest_eligible_ensemble_input_mark(
        conn,
        city=city,
        target_date=target_date,
        metric=metric,
        decision_time=decision_time,
        day0_remaining_from_iso=day0_remaining_from_iso,
    )
    return None if mark is None else mark[1]


def _replacement_live_input_lag_reason(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    posterior_source_cycle_time: object,
    posterior_computed_at: object | None = None,
    posterior_provenance: Mapping[str, object] | None = None,
    held_redecision: bool = False,
    refresh_reasons: list[str] | None = None,
    input_witness_out: dict[str, object] | None = None,
    posterior_provenance_digest: str | None = None,
    use_memo: bool = True,
) -> str | None:
    """Intrinsic consumed-proof verdict, then (only if asked) successor debt.

    The successor census never decides serving under the continuity law, so
    it runs only for callers that collect ``refresh_reasons``.
    """
    census = refresh_reasons is not None
    refresh_reasons = [] if refresh_reasons is None else refresh_reasons
    if not isinstance(held_redecision, bool):
        raise TypeError("held_redecision must be bool")
    posterior_cycle = _parse_source_cycle_utc(posterior_source_cycle_time)
    if posterior_cycle is None:
        return f"posterior_source_cycle_unparseable={posterior_source_cycle_time!s}"
    posterior_computed = _parse_source_cycle_utc(posterior_computed_at)
    if (metric not in {"high", "low"} or decision_time.tzinfo is None or
        posterior_computed is None or not posterior_cycle <= posterior_computed):
        return "basis=posterior_scope_or_materialization_clock_unverifiable"
    provenance = posterior_provenance
    if provenance is None:
        provenance = _posterior_provenance_for_cycle(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            posterior_source_cycle_time=posterior_source_cycle_time,
            posterior_computed_at=posterior_computed_at,
        )
        if provenance is None:
            return "basis=posterior_provenance_unverifiable"
    if not isinstance(provenance, Mapping) or not _provenance_has_current_value_serving(provenance):
        return "basis=current_value_serving_provenance_unverifiable"
    fusion = provenance.get("bayes_precision_fusion")
    shape = (
        fusion.get("current_evidence_shape")
        if isinstance(fusion, Mapping)
        else None
    )
    consumed_ensemble_cycle = (
        _parse_source_cycle_utc(shape.get("source_cycle_time"))
        if isinstance(shape, Mapping)
        else None
    )
    if consumed_ensemble_cycle is None:
        return "basis=current_ensemble_snapshot_provenance_unverifiable"
    if provenance.get("openmeteo_anchor_artifact_id") is None:
        return "basis=openmeteo_anchor_artifact_provenance_unverifiable"

    reason, anchor_cycle = _consumed_proof_verdict(
        conn, city=city, target_date=target_date, metric=metric,
        decision_time=decision_time, posterior_computed=posterior_computed,
        provenance=provenance,
        provenance_digest=posterior_provenance_digest if posterior_provenance is not None else None,
        use_memo=use_memo,
    )
    if input_witness_out is not None:
        serving = fusion.get("current_value_serving") if isinstance(fusion, Mapping) else None
        input_witness_out.update(
            consumed_source_cycle_time=posterior_cycle.isoformat(),
            consumed_ensemble_cycle_time=consumed_ensemble_cycle.isoformat(),
            posterior_computed_at=posterior_computed.isoformat(),
            posterior_age_hours=(decision_time-posterior_computed).total_seconds()/3600.0,
            consumed_model_cycles={model: item.get("served_cycle")
                for model, item in (serving or {}).items() if isinstance(item, Mapping)},
            source_cycle_age_hours=(decision_time-posterior_cycle).total_seconds()/3600.0,
        )
    if reason is not None or not census:
        return reason
    # The verdict above already refused an invalid window, so this is its tau.
    from src.data.replacement_current_value_serving import day0_remaining_from_provenance
    day0_tau, _window_reason = day0_remaining_from_provenance(
        provenance, city=city, target_date=target_date, metric=metric,
        posterior_computed_at=posterior_computed,
    )
    try:
        latest_ensemble_mark = _latest_eligible_ensemble_input_mark(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            decision_time=decision_time,
            day0_remaining_from_iso=day0_tau,
        )
    except sqlite3.OperationalError as exc:
        refresh_reasons.append("basis=successor_ensemble_frontier_unavailable:" + type(exc).__name__)
        latest_ensemble_mark = None
    if input_witness_out is not None:
        input_witness_out["latest_eligible_ensemble_cycle_time"] = (
            latest_ensemble_mark[1].isoformat() if latest_ensemble_mark else None)
        input_witness_out["ensemble_cycle_lag_hours"] = (
            max(0.0,(latest_ensemble_mark[1]-consumed_ensemble_cycle).total_seconds()/3600.0)
            if latest_ensemble_mark else None)
    if latest_ensemble_mark is not None and latest_ensemble_mark[1] > consumed_ensemble_cycle:
        latest_snapshot_id, latest_ensemble_cycle = latest_ensemble_mark
        lag_hours = (
            latest_ensemble_cycle - consumed_ensemble_cycle
        ).total_seconds() / 3600.0
        refresh_reasons.append(
            "basis=current_ensemble_snapshot_superseded:"
            f"latest_snapshot_id={latest_snapshot_id}:"
            f"latest_ensemble_cycle={latest_ensemble_cycle.isoformat()}:"
            f"consumed_ensemble_cycle={consumed_ensemble_cycle.isoformat()}:"
            f"lag_h={lag_hours:.2f}"
        )
    _exact_current_value_serving_lag(
        conn,
        city=city,
        target_date=target_date,
        metric=metric,
        decision_time=decision_time,
        posterior_computed_at=posterior_computed,
        provenance=provenance,
        refresh_reasons=refresh_reasons,
        input_witness_out=input_witness_out,
        consumed_proof_verified=True,
    )
    try:
        artifact_cycle = latest_raw_artifact_input_cycle(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            decision_time=decision_time,
        )
    except sqlite3.OperationalError as exc:
        refresh_reasons.append("basis=successor_anchor_frontier_unavailable:" + type(exc).__name__)
        artifact_cycle = None
    artifact_reference_cycle = anchor_cycle
    if artifact_cycle is not None and artifact_cycle > artifact_reference_cycle:
        lag_hours = (
            artifact_cycle - artifact_reference_cycle
        ).total_seconds() / 3600.0
        refresh_reasons.append(
            "basis=source_cycle_time_raw_forecast_artifacts_lag:"
            f"latest_raw_cycle={artifact_cycle.isoformat()}:"
            f"posterior_cycle={posterior_cycle.isoformat()}:"
            f"consumed_anchor_cycle={artifact_reference_cycle.isoformat()}:"
            f"lag_h={lag_hours:.2f}"
        )
    return None


def _consumed_proof_verdict(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    posterior_computed: datetime,
    provenance: Mapping[str, object],
    provenance_digest: str | None = None,
    use_memo: bool = True,
) -> tuple[str | None, datetime | None]:
    """(blocking reason, consumed anchor cycle) from the posterior's own evidence.

    Station ground is replayed at ``decision_time``; every other read is the
    exact consumed evidence cut at ``posterior_computed``. No successor read.
    A valid verdict is remembered under every input it read except clocks:
    the posterior's provenance and materialization cut, its family, the
    authority configuration and the current ground it was judged against are
    the key; every SQL read and file it made are replayed on each hit (see
    ``reads_hold``). Only the canonical store is remembered: a connection
    wrapper or view is not, and ``use_memo=False`` revalidates from source.
    """
    from src.data.station_ground_evidence import forecast_db_from_connection

    fusion = provenance.get("bayes_precision_fusion")
    shape = fusion.get("current_evidence_shape") if isinstance(fusion, Mapping) else None
    ground: object = ()
    if isinstance(shape, Mapping) and isinstance(shape.get("provider_geometry_evidence"), Mapping):
        ground = _current_station_ground_state(
            conn, city=city, target_date=target_date, decision_time=decision_time,
        )
    db_path = forecast_db_from_connection(conn)
    canonical = use_memo and not _FRESH_SOURCE.get() and type(conn) is sqlite3.Connection
    key = None if not canonical or db_path is None or ground is None else (
        str(db_path), provenance_digest or provenance_identity(provenance),
        posterior_computed.isoformat(), city, str(target_date), metric,
        authority_config_identity(), ground,
    )
    if key is not None:
        cached = _memo_get(_VERDICT_MEMO, key)
        if cached is not None and reads_hold(cached[1], conn):
            return None, cached[0]
    record = ReadRecord() if key is not None else None
    with recorded_reads(record, conn) as seen:
        checked, reason, _anchor = _exact_current_value_serving_lag(
            seen or conn, city=city, target_date=target_date, metric=metric,
            decision_time=decision_time, posterior_computed_at=posterior_computed,
            provenance=provenance,
        )
        if reason is not None:
            return reason, None
        if not checked:
            return "basis=current_value_serving_provenance_unverifiable", None
        # The consumed anchor was possessed when the posterior was computed;
        # that cut, not the decision clock, is its causality bound.
        reason, anchor_cycle = _exact_consumed_anchor_artifact_cycle(
            seen or conn, city=city, target_date=target_date, metric=metric,
            decision_time=posterior_computed, provenance=provenance,
            posterior_computed_at=posterior_computed,
        )
    if reason is not None:
        return reason, None
    if anchor_cycle is None:
        return "basis=openmeteo_anchor_artifact_provenance_unverifiable", None
    if record is not None and record.replayable:
        _memo_put(_VERDICT_MEMO, key, (anchor_cycle, record.frozen()))
    return None, anchor_cycle


def replacement_live_input_lag_reason(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    posterior_source_cycle_time: object,
    posterior_computed_at: object | None = None,
    posterior_provenance: Mapping[str, object] | None = None,
    held_redecision: bool = False,
    input_witness_out: dict[str, object] | None = None,
    successor_census: bool = False,
    posterior_provenance_digest: str | None = None,
    use_memo: bool = True,
) -> str | None:
    """Return intrinsic invalidity only; successor debt is independent evidence.

    Same policy for ENTRY, HELD and standing rests. This does not certify an
    arbitrary row: the bundle reader still requires its exact READY binding,
    live-grade semantics, original dependencies, scope and validity interval.
    A posterior first computed after ``decision_time`` was not possessed then.
    ``successor_census`` adds refresh debt to the witness; it never changes
    the returned reason, so only an actuation-time record asks for it.
    ``posterior_provenance_digest`` names the exact text ``posterior_provenance``
    was parsed from, so the verdict memo need not re-serialize it.
    ``use_memo=False`` revalidates the consumed proof from source.
    """
    computed = _parse_source_cycle_utc(posterior_computed_at)
    if computed is not None and decision_time.tzinfo is not None and computed > decision_time:
        return "basis=posterior_scope_or_materialization_clock_unverifiable"
    return _input_hwm_reason(
        conn, city=city, target_date=target_date, metric=metric,
        decision_time=decision_time,
        posterior_source_cycle_time=posterior_source_cycle_time,
        posterior_computed_at=posterior_computed_at,
        posterior_provenance=posterior_provenance,
        held_redecision=held_redecision, input_witness_out=input_witness_out,
        successor_census=successor_census,
        posterior_provenance_digest=posterior_provenance_digest,
        use_memo=use_memo,
    )


def _input_hwm_reason(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    decision_time: datetime,
    posterior_source_cycle_time: object,
    posterior_computed_at: object | None = None,
    posterior_provenance: Mapping[str, object] | None = None,
    held_redecision: bool = False,
    input_witness_out: dict[str, object] | None = None,
    successor_census: bool = True,
    posterior_provenance_digest: str | None = None,
    use_memo: bool = True,
) -> str | None:
    refresh: list[str] = []
    witness: dict[str, object] = {
        "revision": "validated_posterior_input_continuity_v1",
        "city": city, "target_date": str(target_date), "metric": metric,
        "checked_at": decision_time.isoformat(),
    }
    try:
        reason = _replacement_live_input_lag_reason(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            decision_time=decision_time,
            posterior_source_cycle_time=posterior_source_cycle_time,
            posterior_computed_at=posterior_computed_at,
            posterior_provenance=posterior_provenance,
            held_redecision=held_redecision,
            refresh_reasons=refresh if successor_census else None,
            input_witness_out=witness,
            posterior_provenance_digest=posterior_provenance_digest,
            use_memo=use_memo,
        )
    except ReplacementInputHwmReadUnavailable as exc:
        reason = exc.blocker_reason()
    if successor_census:
        witness["refresh_reasons"] = tuple(dict.fromkeys(refresh))
    witness["blocking_reason"] = reason
    # Diagnostic identity is deliberately not the posterior's content identity.
    witness["witness_identity"] = hashlib.sha256(json.dumps(
        witness, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if input_witness_out is not None:
        input_witness_out.update(witness)
    return reason


def replacement_input_refresh_reason(conn: sqlite3.Connection, **kwargs: object) -> str | None:
    """Coverage/queue projection: a usable old posterior does not cover new inputs.

    ``decision_time`` is the requested coverage clock; a posterior computed
    after it is the coverage being asked about, not an unpossessed one.
    """
    witness: dict[str, object] = {}
    reason = _input_hwm_reason(conn, **kwargs, input_witness_out=witness)
    refresh = witness.get("refresh_reasons", ())
    return reason or (refresh[0] if refresh else None)



def retired_low_uncertified_incumbent_yields_to_current_ensemble(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    incoming_baseline_source_run_id: str,
    decision_time: datetime,
    incumbent_posterior_id: int | None = None,
) -> bool:
    """Prove the sole lawful LOW cycle rollback: uncertified -> window-v2.

    A newer clock from the retired pre-window LOW dataset cannot suppress an
    older clock from the current window-v2 dataset.  This is deliberately not a
    general data-version escape hatch: both revisions must be coordinate-bound
    to the same manifest SHA, the incumbent must bind an exact target snapshot,
    and the incoming run must be the current authority-selected exact snapshot.
    Any absent, unreadable, or stale proof preserves the ordinary cycle HWM.
    """
    if metric != "low" or not incoming_baseline_source_run_id:
        return False
    try:
        from src.contracts.ensemble_snapshot_provenance import (
            ECMWF_OPENDATA_LOW_DATA_VERSION,
            ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED,
            split_coordinate_bound_data_version,
        )
        from src.data.replacement_forecast_source_run_identity import (
            expected_replacement_dependency_identity_by_role,
            validate_replacement_source_run_identity,
        )

        expected = expected_replacement_dependency_identity_by_role(metric)["baseline_b0"]
        expected_version = expected.data_version
        expected_identity = split_coordinate_bound_data_version(str(expected_version or ""))
        if expected_identity is None or expected_identity[0] != ECMWF_OPENDATA_LOW_DATA_VERSION:
            return False
        target_date_text = str(target_date)
        if incumbent_posterior_id is None:
            incumbent = conn.execute(
                """
                SELECT posterior_id, dependency_source_run_ids_json, provenance_json
                  FROM forecast_posteriors
                 WHERE source_id = 'openmeteo_ecmwf_ifs9_bayes_fusion'
                   AND runtime_layer = 'live' AND city = ? AND target_date = ?
                   AND temperature_metric = ? AND datetime(computed_at) <= datetime(?)
                 ORDER BY computed_at DESC, posterior_id DESC LIMIT 1
                """,
                (city, target_date_text, metric, decision_time.astimezone(UTC).isoformat()),
            ).fetchone()
        else:
            incumbent = conn.execute(
                """SELECT posterior_id, dependency_source_run_ids_json, provenance_json
                     FROM forecast_posteriors
                    WHERE posterior_id = ?
                      AND source_id = 'openmeteo_ecmwf_ifs9_bayes_fusion'
                      AND runtime_layer = 'live' AND city = ? AND target_date = ?
                      AND temperature_metric = ? AND datetime(computed_at) <= datetime(?) LIMIT 1""",
                (incumbent_posterior_id, city, target_date_text, metric, decision_time.astimezone(UTC).isoformat()),
            ).fetchone()
        if incumbent is None:
            return False
        dependencies = json.loads(str(incumbent[1] if not hasattr(incumbent, "keys") else incumbent["dependency_source_run_ids_json"]))
        provenance = json.loads(str(incumbent[2] if not hasattr(incumbent, "keys") else incumbent["provenance_json"]))
        if not isinstance(dependencies, Mapping) or not isinstance(provenance, Mapping):
            return False
        fusion = provenance.get("bayes_precision_fusion")
        shape = fusion.get("current_evidence_shape") if isinstance(fusion, Mapping) else None
        incumbent_run_id = str(dependencies.get("baseline_b0") or "").strip()
        incumbent_snapshot_id = dependencies.get("current_ensemble_snapshot")
        if (
            not incumbent_run_id
            or not isinstance(incumbent_snapshot_id, int)
            or not isinstance(shape, Mapping)
            or shape.get("snapshot_id") != incumbent_snapshot_id
        ):
            return False
        incumbent_run = conn.execute(
            "SELECT * FROM source_run WHERE source_run_id = ? LIMIT 1", (incumbent_run_id,)
        ).fetchone()
        if incumbent_run is None or not hasattr(incumbent_run, "keys"):
            return False
        # Match the current ENS authority's durable-possession clock priority.
        incumbent_clock = next(
            (
                incumbent_run[column]
                for column in (
                    "imported_at", "fetch_finished_at", "captured_at", "source_available_at",
                )
                if column in incumbent_run.keys() and incumbent_run[column] is not None
            ),
            None,
        )
        incumbent_available_at = _parse_source_cycle_utc(incumbent_clock)
        if incumbent_available_at is None or incumbent_available_at > decision_time.astimezone(UTC):
            return False
        coverage = conn.execute(
            """
            SELECT * FROM source_run_coverage
             WHERE source_run_id = ? AND lower(city) = lower(?)
               AND target_local_date = ? AND temperature_metric = ?
               AND datetime(recorded_at) <= datetime(?)
               AND datetime(computed_at) <= datetime(?)
             ORDER BY recorded_at DESC LIMIT 1
            """,
            (
                incumbent_run_id, city, target_date_text, metric,
                decision_time.astimezone(UTC).isoformat(),
                decision_time.astimezone(UTC).isoformat(),
            ),
        ).fetchone()
        if incumbent_run is None or coverage is None:
            return False
        incumbent_version = str(
            (incumbent_run["dataset_id"] if hasattr(incumbent_run, "keys") else "") or ""
        )
        incumbent_identity = split_coordinate_bound_data_version(incumbent_version)
        if (
            incumbent_identity is None
            or incumbent_identity[0] != ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED
            or incumbent_identity[1] != expected_identity[1]
        ):
            return False
        if str(coverage["data_version"] if hasattr(coverage, "keys") else "") != incumbent_version:
            return False
        incumbent_validity = validate_replacement_source_run_identity(
            role="baseline_b0", temperature_metric=metric,
            source_run=dict(incumbent_run), coverage=dict(coverage),
        )
        permitted = {
            "REPLACEMENT_SOURCE_RUN_DATA_VERSION_MISMATCH",
            "REPLACEMENT_SOURCE_RUN_COVERAGE_DATA_VERSION_MISMATCH",
        }
        if not set(incumbent_validity.reason_codes) or not set(incumbent_validity.reason_codes).issubset(permitted):
            return False
        snapshot = conn.execute(
            """
            SELECT snapshot_id FROM ensemble_snapshots
             WHERE snapshot_id = ? AND source_run_id = ?
               AND lower(city) = lower(?) AND target_date = ?
               AND temperature_metric = ? AND dataset_id = ?
               AND source_id = 'ecmwf_open_data' AND model_version = 'ecmwf_ens'
               AND datetime(source_available_at) <= datetime(?)
               AND datetime(recorded_at) <= datetime(?)
             LIMIT 1
            """,
            (incumbent_snapshot_id, incumbent_run_id, city, target_date_text,
             metric, incumbent_version, decision_time.astimezone(UTC).isoformat(),
             decision_time.astimezone(UTC).isoformat()),
        ).fetchone()
        if snapshot is None:
            return False
        snapshot_ids = json.loads(str(coverage["snapshot_ids_json"] if hasattr(coverage, "keys") else ""))
        if not isinstance(snapshot_ids, list) or len(snapshot_ids) != 1 or str(snapshot_ids[0]) != str(snapshot[0]):
            return False
        mark = _latest_eligible_ensemble_input_mark(
            conn, city=city, target_date=target_date, metric=metric,
            decision_time=decision_time,
        )
        if mark is None:
            return False
        current_snapshot = conn.execute(
            """SELECT source_run_id FROM ensemble_snapshots
                 WHERE snapshot_id = ? AND lower(city) = lower(?) AND target_date = ?
                   AND temperature_metric = ? AND dataset_id = ? LIMIT 1""",
            (mark[0], city, target_date_text, metric, expected_version),
        ).fetchone()
        return (
            current_snapshot is not None
            and str(current_snapshot[0]) == str(incoming_baseline_source_run_id)
        )
    except (sqlite3.Error, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False
