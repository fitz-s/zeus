#!/usr/bin/env python3
# Lifecycle: created=2026-06-06; last_reviewed=2026-08-29; last_reused=2026-08-29
# Purpose: Materialize replacement live forecast posteriors and publish commit wakes.
# Reuse: Inspect forecast materialization and reactor-wake contracts before changing.
"""Materialize Open-Meteo ECMWF IFS 9km + Bayes fusion posterior."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable, ContextManager, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: E402
    build_anchor_request,
    extract_openmeteo_ecmwf_ifs9_localday_anchor,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload,
)
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (  # noqa: E402
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_current_value_serving import (  # noqa: E402
    CurrentValueServingSchema,
    current_value_serving_schema,
    read_current_instrument_family_latest_id,
    read_current_instrument_frontier_identity,
    read_current_instrument_frontier_sentinel_ids,
)
from src.data.replacement_forecast_materializer import (  # noqa: E402
    CurrentEvidenceSnapshotIdentity,
    PreparedReplacementForecastMaterialization,
    PreparedReplacementForecastSnapshotStale,
    ReplacementForecastMaterializeRequest,
    ReplacementForecastMaterializeResult,
    _ensure_replacement_frontier_indexes,
    _ensure_replacement_identity_columns,
    day0_enqueue_ownership_witness_from_payload,
    prepare_replacement_forecast_live,
    read_current_evidence_snapshot_id,
    read_current_evidence_snapshot_identity,
    write_prepared_replacement_forecast_live,
)
from src.data.raw_forecast_artifact_manifest import read_manifest, write_manifest_to_db  # noqa: E402


UTC = timezone.utc
_SNAPSHOT_RETRY_LIMIT = 3
_IMMEDIATE_BUSY_TIMEOUT_MS = 10
_IMMEDIATE_RETRY_LIMIT = 100
_IMMEDIATE_RETRY_DELAY_SECONDS = 0.05
_WriterLockFactory = Callable[[], ContextManager[None]]
_WRITE_DEFERRED_REASON = "REPLACEMENT_FORECAST_WRITE_DEFERRED"
_STAGE_RECEIPT_SUFFIX = ".stage"


class MaterializationDeadlineExceeded(RuntimeError):
    """The queue-owned absolute deadline elapsed inside a named child stage."""

    def __init__(self, stage: str, deadline_at: datetime) -> None:
        self.stage = stage
        self.deadline_at = deadline_at
        super().__init__(f"REPLACEMENT_LIVE_MATERIALIZATION_DEADLINE_{stage.upper()}")


class _SQLiteDeadlineGuard:
    """Interrupt a blocked SQLite call before the queue's outer child kill."""

    def __init__(self, conn, receipt: "_StageReceipt") -> None:
        self._conn = conn
        self._receipt = receipt
        self._stop = Event()
        self._lock = Lock()
        self._generation = 0
        self._active_generation = 0
        self._thread: Thread | None = None

    def __enter__(self) -> "_SQLiteDeadlineGuard":
        if self._receipt.deadline_at is None:
            return self
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._active_generation = generation

        def _interrupt_at_deadline() -> None:
            deadline = self._receipt.deadline_at
            assert deadline is not None
            remaining = max(
                0.0, (deadline - datetime.now(UTC)).total_seconds()
            )
            if self._stop.wait(remaining):
                return
            # The generation fence makes it impossible for a late watchdog to
            # interrupt a connection after its owning materialization returned.
            with self._lock:
                if self._active_generation != generation or self._stop.is_set():
                    return
                self._conn.interrupt()

        def _interrupt_when_expired() -> int:
            return int(self._receipt.deadline_expired())

        self._conn.set_progress_handler(_interrupt_when_expired, 1_000)
        self._thread = Thread(
            target=_interrupt_at_deadline,
            name="replacement-materialize-deadline",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        with self._lock:
            self._active_generation = 0
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._receipt.deadline_at is not None:
            self._conn.set_progress_handler(None, 0)


@dataclass
class _StageReceipt:
    """Atomic, non-canonical progress evidence retained with one queue request."""

    input_json: Path
    deadline_at: datetime | None
    stage: str = "open_read_snapshot"

    @property
    def path(self) -> Path:
        return Path(f"{self.input_json}{_STAGE_RECEIPT_SUFFIX}")

    @property
    def request_id(self) -> str:
        name = self.input_json.name
        if ".timeout-retry-" not in name:
            return name
        return f"{name.split('.timeout-retry-', 1)[0]}{self.input_json.suffix}"

    def mark(self, stage: str) -> None:
        self.stage = stage
        payload = {
            "schema_version": 1,
            "request_id": self.request_id,
            "input_json": str(self.input_json),
            "stage": stage,
            "deadline_at": (
                None
                if self.deadline_at is None
                else self.deadline_at.astimezone(UTC).isoformat()
            ),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def deadline_expired(self) -> bool:
        return self.deadline_at is not None and datetime.now(UTC) >= self.deadline_at

    def require_budget(self) -> None:
        if self.deadline_expired():
            raise MaterializationDeadlineExceeded(self.stage, self.deadline_at)

    def sqlite_deadline_guard(self, conn) -> _SQLiteDeadlineGuard:
        return _SQLiteDeadlineGuard(conn, self)


class ReplacementForecastWriteDeferred(RuntimeError):
    """The canonical writer was busy; the queue must retry this request."""


@contextlib.contextmanager
def _forecast_writer_lock():
    """Own the forecasts LIVE flock only for a canonical write transaction."""

    from src.state.db import ZEUS_FORECASTS_DB_PATH
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    # The surrounding transaction helper owns the bounded retry loop.  A
    # blocking flock here would bypass that bound and let one background
    # materialization retain the queue worker behind another writer forever.
    with db_writer_lock(
        ZEUS_FORECASTS_DB_PATH,
        WriteClass.LIVE,
        blocking=False,
    ):
        yield


def _is_sqlite_writer_contention(exc: sqlite3.OperationalError) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(error_code, int) and error_code & 0xFF in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }:
        return True
    message = str(exc).casefold()
    return "locked" in message or "busy" in message


@contextlib.contextmanager
def _bounded_sqlite_writer_wait(conn):
    """Temporarily bound this connection's SQLite writer wait."""

    prior_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    conn.execute(f"PRAGMA busy_timeout = {_IMMEDIATE_BUSY_TIMEOUT_MS}")
    try:
        yield
    finally:
        conn.execute(f"PRAGMA busy_timeout = {prior_timeout_ms}")


@contextlib.contextmanager
def _immediate_writer_transaction(conn, writer_lock: _WriterLockFactory):
    """Retry SQLite ownership outside the priority LIVE flock."""

    last_contention: BaseException | None = None
    with _bounded_sqlite_writer_wait(conn):
        for attempt in range(_IMMEDIATE_RETRY_LIMIT):
            contention: BaseException | None = None
            lock_stack = contextlib.ExitStack()
            try:
                lock_stack.enter_context(writer_lock())
            except BlockingIOError as exc:
                lock_stack.close()
                contention = exc
            else:
                with lock_stack:
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                    except sqlite3.OperationalError as exc:
                        if not _is_sqlite_writer_contention(exc):
                            raise
                        contention = exc
                    else:
                        try:
                            yield
                        except sqlite3.OperationalError as exc:
                            if conn.in_transaction:
                                conn.rollback()
                            if _is_sqlite_writer_contention(exc):
                                raise ReplacementForecastWriteDeferred(
                                    _WRITE_DEFERRED_REASON
                                ) from exc
                            raise
                        except BaseException:
                            if conn.in_transaction:
                                conn.rollback()
                            raise
                        finally:
                            if conn.in_transaction:
                                conn.rollback()
                        return
            last_contention = contention
            if attempt + 1 < _IMMEDIATE_RETRY_LIMIT:
                time.sleep(_IMMEDIATE_RETRY_DELAY_SECONDS)

    # SCOPE: this materializer's one pending write transaction only.
    # DRAIN: every failed acquisition releases the LIVE flock before a bounded
    # retry; the queue retries the item on its normal cadence after exhaustion.
    # RESET: any later attempt that obtains both locks enters the transaction.
    raise ReplacementForecastWriteDeferred(
        _WRITE_DEFERRED_REASON
    ) from last_contention


def _attach_world_read_only(conn) -> None:
    """Expose current observation truth without widening the forecast write lock."""

    from src.state.db import ZEUS_WORLD_DB_PATH

    attached = {
        str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()
    }
    if "world" in attached:
        return
    world_uri = f"{ZEUS_WORLD_DB_PATH.resolve().as_uri()}?mode=ro"
    conn.execute("ATTACH DATABASE ? AS world", (world_uri,))


@dataclass(frozen=True)
class TemperatureBin:
    bin_id: str
    lower_c: float | None
    upper_c: float | None
    center_c: float | None
    display_unit: str = "C"
    settlement_unit: str = "C"
    rounding_rule: str = "wmo_half_up"


@dataclass(frozen=True)
class _DurablePreparationReceipt:
    """Canonical upstream facts committed before the posterior transaction."""

    schema_ready: bool
    anchor_artifact_id: int | None
    manifest_committed: bool


@dataclass(frozen=True)
class _SourceRunWitness:
    source_run_id: str | None
    state: str
    fetch_finished_at: str | None
    requested_available_at: str


@dataclass(frozen=True)
class _ExactRowsWitness:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class _TargetDependencyWitness:
    """Exact rows plus bounded canonical frontier IDs for one prepared q."""

    source_runs: _ExactRowsWitness
    source_run_states: tuple[_SourceRunWitness, ...]
    anchor_artifact: _ExactRowsWitness
    provider_rows: _ExactRowsWitness
    ensemble_snapshot: _ExactRowsWitness
    provider_models: tuple[str, ...]
    provider_frontier: tuple[tuple[str, int | None], ...]
    provider_sentinels: tuple[tuple[str, int], ...]
    provider_family_latest_id: int | None
    ensemble_frontier_id: int | None
    ensemble_identity: CurrentEvidenceSnapshotIdentity | None
    provider_schema: CurrentValueServingSchema
    prepared_provider_row_ids: tuple[int, ...]
    prepared_snapshot_id: int | None
    prepared_shape_id: str | None


class _TargetDependencyWitnessUnavailable(RuntimeError):
    """A bounded target witness could not be read completely."""


def _utc_iso(value: datetime | str, *, field_name: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


def _table_columns(conn, table: str) -> tuple[str, ...]:
    try:
        columns = tuple(
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
        )
    except Exception as exc:
        raise _TargetDependencyWitnessUnavailable(
            f"{table} schema witness unavailable"
        ) from exc
    if not columns:
        raise _TargetDependencyWitnessUnavailable(f"{table} is unavailable")
    return columns


def _exact_rows_witness(
    conn,
    *,
    table: str,
    pk: str,
    ids: tuple[object, ...],
    columns: tuple[str, ...],
) -> _ExactRowsWitness:
    if not ids:
        return _ExactRowsWitness(columns, ())
    if pk not in columns:
        raise _TargetDependencyWitnessUnavailable(f"{table}.{pk} unavailable")
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _value in ids)
    try:
        rows = conn.execute(
            f"SELECT {quoted_columns} FROM {table} "
            f"WHERE {pk} IN ({placeholders}) ORDER BY {pk}",
            ids,
        ).fetchall()
    except Exception as exc:
        raise _TargetDependencyWitnessUnavailable(
            f"{table} exact-row witness unavailable"
        ) from exc
    return _ExactRowsWitness(
        columns,
        tuple(tuple(row[index] for index in range(len(columns))) for row in rows),
    )


def _source_run_states(
    witness: _ExactRowsWitness,
    *,
    requested: tuple[tuple[str | None, object], ...],
) -> tuple[_SourceRunWitness, ...]:
    id_index = witness.columns.index("source_run_id")
    fetch_index = witness.columns.index("fetch_finished_at")
    by_id = {str(row[id_index]): row for row in witness.rows}
    states: list[_SourceRunWitness] = []
    for source_run_id, source_available_at in requested:
        if not source_run_id:
            states.append(
                _SourceRunWitness(
                    source_run_id, "not_requested", None, str(source_available_at)
                )
            )
            continue
        row = by_id.get(source_run_id)
        if row is None:
            state = "missing"
            fetch_finished_at = None
        elif row[fetch_index] is None or not str(row[fetch_index]).strip():
            state = "present_empty"
            fetch_finished_at = None
        else:
            state = "present"
            fetch_finished_at = str(row[fetch_index])
        states.append(
            _SourceRunWitness(
                source_run_id,
                state,
                fetch_finished_at,
                str(source_available_at),
            )
        )
    return tuple(states)


def _build_target_dependency_witness(
    conn,
    prepared: PreparedReplacementForecastMaterialization,
    *,
    columns: Mapping[str, tuple[str, ...]] | None = None,
    provider_schema: CurrentValueServingSchema | None = None,
    baseline: _TargetDependencyWitness | None = None,
) -> _TargetDependencyWitness:
    request = prepared.request
    fusion = (prepared.posterior.provenance_payload or {}).get(
        "bayes_precision_fusion"
    ) or {}
    if not isinstance(fusion, Mapping):
        raise _TargetDependencyWitnessUnavailable("fusion witness unavailable")
    prepared_provider_ids = tuple(
        sorted(int(value) for value in fusion.get("raw_model_forecast_ids", ()))
    )
    current_shape = fusion.get("current_evidence_shape") or {}
    if not isinstance(current_shape, Mapping):
        raise _TargetDependencyWitnessUnavailable("shape witness unavailable")
    prepared_snapshot_id = (
        None
        if current_shape.get("snapshot_id") is None
        else int(current_shape["snapshot_id"])
    )
    provider_schema = provider_schema or current_value_serving_schema(conn)
    table_columns = dict(columns or {})
    for table in (
        "source_run",
        "raw_forecast_artifacts",
        "raw_model_forecasts",
        "ensemble_snapshots",
    ):
        if table not in table_columns:
            table_columns[table] = _table_columns(conn, table)

    source_requests = (
        (
            request.baseline_source_run_id,
            request.baseline_source_available_at,
        ),
        (
            request.openmeteo_source_run_id,
            request.openmeteo_source_available_at,
        ),
    )
    source_ids = tuple(
        sorted({str(run_id) for run_id, _available in source_requests if run_id})
    )
    source_runs = _exact_rows_witness(
        conn,
        table="source_run",
        pk="source_run_id",
        ids=source_ids,
        columns=table_columns["source_run"],
    )
    target_date = (
        request.target_date.isoformat()
        if isinstance(request.target_date, date)
        else str(request.target_date)
    )
    provider_family_latest_id = read_current_instrument_family_latest_id(
        conn,
        city=request.city,
        metric=prepared.metric,
        target_date=target_date,
    )
    if baseline is None:
        provider_frontier = read_current_instrument_frontier_identity(
            conn,
            city=request.city,
            metric=prepared.metric,
            target_date=target_date,
            decision_time_iso=_utc_iso(request.computed_at, field_name="computed_at"),
            models=None,
            schema=provider_schema,
        )
        provider_sentinels = read_current_instrument_frontier_sentinel_ids(
            conn,
            city=request.city,
            metric=prepared.metric,
            target_date=target_date,
            decision_time_iso=_utc_iso(request.computed_at, field_name="computed_at"),
            schema=provider_schema,
        )
        provider_models = tuple(
            sorted(
                {
                    *(model for model, _row_id in provider_frontier),
                    *(model for model, _row_id in provider_sentinels),
                }
            )
        )
        ensemble_identity = read_current_evidence_snapshot_identity(
            conn, request, metric=prepared.metric
        )
        ensemble_frontier_id = (
            None if ensemble_identity is None else ensemble_identity.snapshot_id
        )
    else:
        provider_frontier = baseline.provider_frontier
        provider_models = baseline.provider_models
        provider_sentinels = baseline.provider_sentinels
        ensemble_identity = baseline.ensemble_identity
        ensemble_frontier_id = read_current_evidence_snapshot_id(
            conn,
            request,
            metric=prepared.metric,
        )
    provider_identity_ids = tuple(
        sorted(
            {
                *prepared_provider_ids,
                *(row_id for _model, row_id in provider_sentinels),
                *(
                    row_id
                    for _model, row_id in provider_frontier
                    if row_id is not None
                ),
            }
        )
    )
    provider_rows = _exact_rows_witness(
        conn,
        table="raw_model_forecasts",
        pk="raw_model_forecast_id",
        ids=provider_identity_ids,
        columns=table_columns["raw_model_forecasts"],
    )
    ensemble_snapshot = _exact_rows_witness(
        conn,
        table="ensemble_snapshots",
        pk="snapshot_id",
        ids=(() if prepared_snapshot_id is None else (prepared_snapshot_id,)),
        columns=table_columns["ensemble_snapshots"],
    )
    serving = fusion.get("current_value_serving")
    serving_valid = isinstance(serving, Mapping) and bool(serving)
    if serving_valid:
        serving_valid = all(
            isinstance(payload, Mapping)
            and isinstance(payload.get("raw_model_forecast_id"), int)
            and not isinstance(payload.get("raw_model_forecast_id"), bool)
            and payload["raw_model_forecast_id"] > 0
            for payload in serving.values()
        )
    if not serving_valid:
        # A live-ineligible computation can legitimately have no fusion serving
        # witness (for example, the current ENS shape is absent).  It still
        # needs a stable dependency snapshot so the writer can return its typed
        # BLOCKED reasons.  A live-eligible computation without this witness is
        # malformed authority and must remain fail-closed.
        if bool(getattr(prepared.posterior, "live_eligible", True)):
            raise _TargetDependencyWitnessUnavailable(
                "current value serving witness unavailable"
            )
        serving = {}
    prepared_provider_frontier = tuple(
        sorted(
            (
                str(model),
                int(payload["raw_model_forecast_id"]),
            )
            for model, payload in serving.items()
            if isinstance(payload, Mapping)
            and int(payload.get("raw_model_forecast_id", -1))
            in set(prepared_provider_ids)
        )
    )
    current_provider_frontier = dict(provider_frontier)
    prepared_provider_frontier_by_model = dict(prepared_provider_frontier)
    if len(provider_rows.rows) != len(provider_identity_ids) or any(
        current_provider_frontier.get(model) != raw_id
        for model, raw_id in prepared_provider_frontier_by_model.items()
    ):
        raise _TargetDependencyWitnessUnavailable(
            "prepared provider frontier unavailable"
        )
    if prepared_snapshot_id is not None and (
        len(ensemble_snapshot.rows) != 1
        or ensemble_frontier_id != prepared_snapshot_id
    ):
        raise _TargetDependencyWitnessUnavailable(
            "prepared ensemble frontier unavailable"
        )
    anchor_artifact = _exact_rows_witness(
        conn,
        table="raw_forecast_artifacts",
        pk="artifact_id",
        ids=(() if request.anchor_artifact_id is None else (request.anchor_artifact_id,)),
        columns=table_columns["raw_forecast_artifacts"],
    )
    if request.anchor_artifact_id is not None and len(anchor_artifact.rows) != 1:
        raise _TargetDependencyWitnessUnavailable(
            "prepared anchor artifact unavailable"
        )
    return _TargetDependencyWitness(
        source_runs=source_runs,
        source_run_states=_source_run_states(
            source_runs, requested=source_requests
        ),
        anchor_artifact=anchor_artifact,
        provider_rows=provider_rows,
        ensemble_snapshot=ensemble_snapshot,
        provider_models=provider_models,
        provider_frontier=provider_frontier,
        provider_sentinels=provider_sentinels,
        provider_family_latest_id=provider_family_latest_id,
        ensemble_frontier_id=ensemble_frontier_id,
        ensemble_identity=ensemble_identity,
        provider_schema=provider_schema,
        prepared_provider_row_ids=prepared_provider_ids,
        prepared_snapshot_id=prepared_snapshot_id,
        prepared_shape_id=str(current_shape.get("shape_hash") or ""),
    )


def _target_dependency_witness(
    conn,
    prepared: object,
) -> _TargetDependencyWitness | object:
    """Read only target-keyed dependency identities; never recompute q."""

    if not isinstance(prepared, PreparedReplacementForecastMaterialization):
        return prepared
    return _build_target_dependency_witness(conn, prepared)


def _revalidate_target_dependency_witness(
    conn,
    prepared: object,
    baseline: _TargetDependencyWitness | object,
) -> _TargetDependencyWitness | object:
    """Re-read exact prepared rows and bounded frontier IDs under final lock."""

    if not isinstance(prepared, PreparedReplacementForecastMaterialization):
        return prepared
    if not isinstance(baseline, _TargetDependencyWitness):
        return baseline
    columns = {
        "source_run": baseline.source_runs.columns,
        "raw_forecast_artifacts": baseline.anchor_artifact.columns,
        "raw_model_forecasts": baseline.provider_rows.columns,
        "ensemble_snapshots": baseline.ensemble_snapshot.columns,
    }
    return _build_target_dependency_witness(
        conn,
        prepared,
        columns=columns,
        provider_schema=baseline.provider_schema,
        baseline=baseline,
    )


def _dt(value: str, *, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_input_path(path_value: object, *, base_dir: Path) -> Path:
    path = Path(str(path_value))
    if path.is_absolute():
        return path
    candidates = [base_dir / path, ROOT / path, Path.cwd() / path]
    if len(path.parts) >= 2 and path.parts[0] == ".." and path.parts[1] == "raw_manifests":
        candidates.append(ROOT / "state" / "replacement_forecast_live" / Path(*path.parts[1:]))
    candidates.append(ROOT / "state" / "replacement_forecast_live" / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    base_candidate = candidates[0]
    return base_candidate


def _bins(payload: Mapping[str, Any]) -> tuple[TemperatureBin, ...]:
    rows = payload.get("bins")
    if not isinstance(rows, list) or not rows:
        raise ValueError("input JSON must contain non-empty bins[]")
    bins: list[TemperatureBin] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("bins[] entries must be objects")
        bins.append(
            TemperatureBin(
                bin_id=str(row["bin_id"]),
                lower_c=None if row.get("lower_c") is None else float(row["lower_c"]),
                upper_c=None if row.get("upper_c") is None else float(row["upper_c"]),
                center_c=None if row.get("center_c") is None else float(row["center_c"]),
                display_unit=str(row.get("display_unit") or "C").strip().upper(),  # type: ignore[arg-type]
                settlement_unit=str(row.get("settlement_unit") or "C").strip().upper(),  # type: ignore[arg-type]
                rounding_rule=str(row.get("rounding_rule") or "wmo_half_up").strip(),  # type: ignore[arg-type]
            )
        )
    return tuple(bins)


def _template() -> dict[str, object]:
    return {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "anchor_weight": 0.80,
        "anchor_sigma_c": 3.00,
        "bins": [
            {"bin_id": "cool", "lower_c": None, "upper_c": 20.0, "center_c": 19.0},
            {"bin_id": "warm", "lower_c": 21.0, "upper_c": 30.0, "center_c": 25.5},
            {"bin_id": "hot", "lower_c": 31.0, "upper_c": None, "center_c": 32.0},
        ],
        "openmeteo_payload_json": "openmeteo_payload.json",
        "precision_metadata_json": "openmeteo_precision_metadata.json",
        "latitude": 31.2304,
        "longitude": 121.4737,
    }


def _publish_materialization_wake(
    request: ReplacementForecastMaterializeRequest,
) -> bool:
    """Wake the reactor immediately after this family's durable commit."""
    try:
        from src.runtime.reactor_wake import publish_reactor_wake

        wake = publish_reactor_wake(
            source="replacement_forecast_materializer",
            reason="forecast_posterior_advanced",
            forecast_families=(
                (
                    request.city,
                    request.target_date.isoformat(),
                    request.temperature_metric,
                ),
            ),
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "forecast posterior committed but per-family reactor wake failed",
            exc_info=True,
        )
        return False
    logging.getLogger(__name__).info(
        "forecast posterior family wake published city=%s date=%s metric=%s id=%s",
        request.city,
        request.target_date,
        request.temperature_metric,
        wake.wake_id,
    )
    return True


def _data_version(conn) -> int:
    return int(conn.execute("PRAGMA data_version").fetchone()[0])


def _prepare_live_schema_and_manifest(
    conn,
    *,
    init_schema: bool,
    schema_ready: bool,
    openmeteo_manifest: object | None,
    anchor_artifact_id: int | None,
    writer_lock: _WriterLockFactory | None = None,
) -> _DurablePreparationReceipt:
    if schema_ready and openmeteo_manifest is None:
        return _DurablePreparationReceipt(
            schema_ready=True,
            anchor_artifact_id=anchor_artifact_id,
            manifest_committed=False,
        )
    transaction = (
        _immediate_writer_transaction(conn, writer_lock)
        if writer_lock is not None
        else contextlib.nullcontext()
    )
    if writer_lock is None:
        conn.execute("BEGIN IMMEDIATE")
    try:
        with transaction:
            if init_schema:
                from src.state.db import _create_readiness_state
                from src.state.schema.v2_schema import (
                    ensure_replacement_forecast_live_schema,
                )

                ensure_replacement_forecast_live_schema(conn)
                _create_readiness_state(conn)
            if not schema_ready:
                _ensure_replacement_identity_columns(conn)
            if openmeteo_manifest is not None:
                anchor_artifact_id = write_manifest_to_db(
                    conn,
                    openmeteo_manifest,
                    root=ROOT,
                    verify_artifact=False,
                )
            conn.commit()
            return _DurablePreparationReceipt(
                schema_ready=True,
                anchor_artifact_id=anchor_artifact_id,
                manifest_committed=openmeteo_manifest is not None,
            )
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def _commit_from_read_snapshot(
    conn,
    request: ReplacementForecastMaterializeRequest,
    *,
    writer_lock: _WriterLockFactory | None = None,
    stage_receipt: _StageReceipt | None = None,
) -> ReplacementForecastMaterializeResult:
    if writer_lock is None:
        raise RuntimeError("REPLACEMENT_FORECAST_WRITER_LOCK_REQUIRED")
    for _attempt in range(_SNAPSHOT_RETRY_LIMIT):
        if stage_receipt is not None:
            stage_receipt.mark("prepare_fusion")
            stage_receipt.require_budget()
        conn.execute("BEGIN")
        try:
            prepared = prepare_replacement_forecast_live(conn, request)
            if isinstance(prepared, ReplacementForecastMaterializeResult):
                return prepared
            if stage_receipt is not None:
                stage_receipt.mark("dependency_witness")
                stage_receipt.require_budget()
            witness = _target_dependency_witness(conn, prepared)
        except sqlite3.OperationalError as exc:
            if stage_receipt is not None and stage_receipt.deadline_expired():
                raise MaterializationDeadlineExceeded(
                    stage_receipt.stage,
                    stage_receipt.deadline_at,
                ) from exc
            raise
        except _TargetDependencyWitnessUnavailable:
            continue
        finally:
            if conn.in_transaction:
                conn.rollback()

        # The posterior build above is intentionally lock-free.  Only the
        # snapshot revalidation and durable write own the process-global writer
        # flock; otherwise four concurrent materializers can blind Day0 exits by
        # starving observation/vector writers for the whole fusion compute.
        if stage_receipt is not None:
            stage_receipt.mark("write_verify")
            stage_receipt.require_budget()
        with _immediate_writer_transaction(conn, writer_lock):
            try:
                try:
                    current_witness = _revalidate_target_dependency_witness(
                        conn, prepared, witness
                    )
                except _TargetDependencyWitnessUnavailable:
                    conn.rollback()
                    continue
                # SCOPE: this city + target_date + metric and only its prepared
                # source-run/artifact/provider/ENS identities.
                # DRAIN: release the final lock, rebuild outside it, retry at most
                # _SNAPSHOT_RETRY_LIMIT times.
                # RESET: an attempt whose exact rows and canonical frontier IDs
                # remain unchanged may commit; unrelated DB writes are invisible.
                if current_witness != witness:
                    conn.rollback()
                    continue
                try:
                    result = write_prepared_replacement_forecast_live(
                        conn, prepared
                    )
                except PreparedReplacementForecastSnapshotStale:
                    conn.rollback()
                    continue
                conn.commit()
                return result
            except Exception as exc:
                if conn.in_transaction:
                    conn.rollback()
                if (
                    isinstance(exc, sqlite3.OperationalError)
                    and stage_receipt is not None
                    and stage_receipt.deadline_expired()
                ):
                    raise MaterializationDeadlineExceeded(
                        stage_receipt.stage,
                        stage_receipt.deadline_at,
                    ) from exc
                raise

    logging.getLogger(__name__).warning(
        "forecast DB changed during %s snapshot retries; deferring materialization for %s %s %s",
        _SNAPSHOT_RETRY_LIMIT,
        request.city,
        request.target_date,
        request.temperature_metric,
    )
    raise RuntimeError("REPLACEMENT_FORECAST_SNAPSHOT_RETRY_EXHAUSTED")


def _dry_run_from_read_snapshot(
    conn,
    request: ReplacementForecastMaterializeRequest,
) -> ReplacementForecastMaterializeResult:
    for _attempt in range(_SNAPSHOT_RETRY_LIMIT):
        version = _data_version(conn)
        conn.execute("BEGIN")
        try:
            prepared = prepare_replacement_forecast_live(conn, request)
        finally:
            if conn.in_transaction:
                conn.rollback()
        if isinstance(prepared, ReplacementForecastMaterializeResult):
            return prepared

        conn.execute("BEGIN IMMEDIATE")
        try:
            if _data_version(conn) != version:
                conn.rollback()
                continue
            result = write_prepared_replacement_forecast_live(conn, prepared)
        finally:
            if conn.in_transaction:
                conn.rollback()
        return result
    raise RuntimeError("REPLACEMENT_FORECAST_SNAPSHOT_RETRY_EXHAUSTED")


def _error_response(
    exc: Exception,
    receipt: _DurablePreparationReceipt | None = None,
) -> dict[str, object]:
    response: dict[str, object] = {
        "status": "ERROR",
        "error_type": exc.__class__.__name__,
        "error": str(exc),
    }
    if isinstance(exc, ReplacementForecastWriteDeferred):
        response["reason_codes"] = [_WRITE_DEFERRED_REASON]
    if receipt is not None:
        response.update(
            {
                "durable_preparation": {
                    "schema_ready": receipt.schema_ready,
                    "openmeteo_anchor_artifact_id": receipt.anchor_artifact_id,
                    "manifest_committed": receipt.manifest_committed,
                },
                "posterior_committed": False,
                "retry_safe": True,
            }
        )
    return response


def _materialize(
    input_json: Path,
    *,
    commit: bool,
    init_schema: bool,
    conn=None,
    publish_wake: bool = True,
    schema_ready: bool = False,
    writer_lock: _WriterLockFactory | None = None,
    stage_receipt: _StageReceipt | None = None,
) -> tuple[int, dict[str, object]]:
    stage_receipt = stage_receipt or _StageReceipt(input_json, None)
    if conn is None:
        from src.state.db import (
            connect_existing_forecasts_db_without_journal_bootstrap,
            get_forecasts_connection,
        )

        # WAL is established at daemon/schema boot. Repeating journal-mode
        # bootstrap here can wait for the full connection busy timeout behind
        # an atomic bulk ENS ingest, before this module's bounded writer retry
        # is active. Open the existing DB directly so one upstream transaction
        # cannot freeze every city's materialization poll.
        stage_receipt.mark("open_read_snapshot")
        stage_receipt.require_budget()
        owned_conn = (
            connect_existing_forecasts_db_without_journal_bootstrap()
            if commit
            else get_forecasts_connection(write_class=None)
        )
        try:
            _attach_world_read_only(owned_conn)
            with stage_receipt.sqlite_deadline_guard(owned_conn):
                return _materialize(
                    input_json,
                    commit=commit,
                    init_schema=init_schema,
                    conn=owned_conn,
                    publish_wake=publish_wake,
                    schema_ready=schema_ready,
                    writer_lock=writer_lock or _forecast_writer_lock,
                    stage_receipt=stage_receipt,
                )
        finally:
            owned_conn.close()
    if commit and writer_lock is None:
        raise RuntimeError("REPLACEMENT_FORECAST_WRITER_LOCK_REQUIRED")
    effective_writer_lock = writer_lock or contextlib.nullcontext
    payload = _load_json(input_json)
    if not isinstance(payload, Mapping):
        raise ValueError("input JSON must decode to an object")
    base_dir = input_json.parent
    openmeteo_manifest = None
    if "openmeteo_manifest_json" in payload:
        openmeteo_manifest = read_manifest(
            _resolve_input_path(
                payload["openmeteo_manifest_json"], base_dir=base_dir
            )
        )
        openmeteo_manifest.verify_artifact(root=ROOT)
    metric = str(payload["temperature_metric"])
    target_date = date.fromisoformat(str(payload["target_date"]))
    source_cycle_time = _dt(str(payload["source_cycle_time"]), field_name="source_cycle_time")
    anchor_artifact_id = (
        None
        if payload.get("openmeteo_anchor_artifact_id") in (None, "")
        else int(payload["openmeteo_anchor_artifact_id"])
    )
    if "openmeteo_payload_json" in payload:
        openmeteo_payload = _load_json(
            _resolve_input_path(payload["openmeteo_payload_json"], base_dir=base_dir)
        )
        if not isinstance(openmeteo_payload, Mapping):
            raise ValueError("Open-Meteo payload JSON must decode to an object")
    else:
        if "latitude" not in payload or "longitude" not in payload:
            raise ValueError("Open-Meteo direct fetch requires latitude and longitude")
        openmeteo_payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(
            build_anchor_request(
                latitude=float(payload["latitude"]),
                longitude=float(payload["longitude"]),
                run=source_cycle_time,
                timezone_name=str(payload["city_timezone"]),
            )
        )
    openmeteo_anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
        openmeteo_payload,
        city_timezone=str(payload["city_timezone"]),
        target_local_date=target_date,
        source_cycle_time=source_cycle_time,
    )
    if "precision_metadata_json" not in payload:
        raise ValueError(
            "input JSON requires precision_metadata_json for Open-Meteo ECMWF IFS 9km anchor"
        )
    precision_payload = _load_json(
        _resolve_input_path(payload["precision_metadata_json"], base_dir=base_dir)
    )
    if not isinstance(precision_payload, Mapping):
        raise ValueError("precision_metadata_json must decode to an object")
    precision_guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(**dict(precision_payload))
    )
    request = ReplacementForecastMaterializeRequest(
        city=str(payload["city"]),
        city_id=str(payload.get("city_id") or payload["city"]),
        city_timezone=str(payload["city_timezone"]),
        target_date=target_date,
        temperature_metric=metric,
        baseline_source_run_id=str(payload["baseline_source_run_id"]),
        baseline_data_version=str(payload["baseline_data_version"]),
        baseline_source_available_at=_dt(
            str(payload["baseline_source_available_at"]),
            field_name="baseline_source_available_at",
        ),
        openmeteo_anchor=openmeteo_anchor,
        openmeteo_source_run_id=str(payload.get("openmeteo_source_run_id") or ""),
        openmeteo_source_available_at=_dt(
            str(payload["openmeteo_source_available_at"]),
            field_name="openmeteo_source_available_at",
        ),
        bins=_bins(payload),
        source_cycle_time=source_cycle_time,
        computed_at=_dt(str(payload["computed_at"]), field_name="computed_at"),
        expires_at=(
            None
            if payload.get("expires_at") is None
            else _dt(str(payload["expires_at"]), field_name="expires_at")
        ),
        openmeteo_precision_guard=precision_guard,
        anchor_weight=float(payload.get("anchor_weight", 0.80)),
        anchor_sigma_c=float(payload.get("anchor_sigma_c", 3.00)),
        settlement_step_c=float(payload.get("settlement_step_c", 1.0)),
        day0_observed_extreme_c=(
            None
            if payload.get("day0_observed_extreme_c") in (None, "")
            else float(payload["day0_observed_extreme_c"])
        ),
        day0_observed_extreme_source=(
            None
            if payload.get("day0_observed_extreme_source") in (None, "")
            else str(payload["day0_observed_extreme_source"])
        ),
        day0_observed_extreme_observation_time=(
            None
            if payload.get("day0_observed_extreme_observation_time") in (None, "")
            else str(payload["day0_observed_extreme_observation_time"])
        ),
        day0_observed_extreme_sample_count=(
            None
            if payload.get("day0_observed_extreme_sample_count") in (None, "")
            else int(payload["day0_observed_extreme_sample_count"])
        ),
        day0_observed_extreme_unit=(
            None
            if payload.get("day0_observed_extreme_unit") in (None, "")
            else str(payload["day0_observed_extreme_unit"])
        ),
        day0_observation_state=(
            None
            if payload.get("day0_observation_state") in (None, "")
            else str(payload["day0_observation_state"])
        ),
        upgrade_trigger=(
            str(payload["upgrade_trigger"]) if payload.get("upgrade_trigger") else None
        ),
        day0_enqueue_owner_witness=day0_enqueue_ownership_witness_from_payload(
            payload
        ),
    )
    wake_published = False
    receipt: _DurablePreparationReceipt | None = None
    try:
        if commit:
            stage_receipt.mark("write_verify")
            stage_receipt.require_budget()
            receipt = _prepare_live_schema_and_manifest(
                conn,
                init_schema=init_schema,
                schema_ready=schema_ready,
                openmeteo_manifest=openmeteo_manifest,
                anchor_artifact_id=anchor_artifact_id,
                writer_lock=effective_writer_lock,
            )
            _ensure_replacement_frontier_indexes(conn)
            conn.commit()
            anchor_artifact_id = receipt.anchor_artifact_id
            if anchor_artifact_id is not None:
                request = replace(request, anchor_artifact_id=anchor_artifact_id)
            result = _commit_from_read_snapshot(
                conn,
                request,
                writer_lock=effective_writer_lock,
                stage_receipt=stage_receipt,
            )
            if result.ok and publish_wake:
                stage_receipt.mark("wake")
                stage_receipt.require_budget()
                wake_published = _publish_materialization_wake(request)
        else:
            if anchor_artifact_id is not None:
                request = replace(request, anchor_artifact_id=anchor_artifact_id)
            stage_receipt.mark("prepare_fusion")
            stage_receipt.require_budget()
            result = _dry_run_from_read_snapshot(conn, request)
    except Exception as exc:
        if conn.in_transaction:
            conn.rollback()
        # A durable manifest write does not make an expired posterior attempt
        # terminal. Preserve the typed DEFERRED result so the queue restores
        # this exact request; converting it to generic ERROR drops the only
        # executable seed after transient writer contention.
        if isinstance(exc, MaterializationDeadlineExceeded):
            raise
        if (
            isinstance(exc, sqlite3.OperationalError)
            and stage_receipt.deadline_expired()
        ):
            raise MaterializationDeadlineExceeded(
                stage_receipt.stage,
                stage_receipt.deadline_at,
            ) from exc
        if receipt is None:
            raise
        return 2, _error_response(exc, receipt)
    response = {
        "status": result.status,
        "reason_codes": list(result.reason_codes),
        "posterior_id": result.posterior_id,
        "anchor_id": result.anchor_id,
        "readiness_id": result.readiness_id,
        "openmeteo_anchor_artifact_id": anchor_artifact_id,
        "committed": commit,
        "reactor_wake_published": wake_published,
        "schema_init_requested": init_schema,
        "schema_ready": bool(commit and receipt is not None and receipt.schema_ready),
        "schema_initialized": bool(
            commit and init_schema and receipt is not None and receipt.schema_ready
        ),
        "durable_preparation": (
            None
            if receipt is None
            else {
                "schema_ready": receipt.schema_ready,
                "openmeteo_anchor_artifact_id": receipt.anchor_artifact_id,
                "manifest_committed": receipt.manifest_committed,
            }
        ),
        "dry_run_scope": (
            None if commit else "read_snapshot_compute_plus_rollback_write_preview"
        ),
        "forecast_family": [
            request.city,
            request.target_date.isoformat(),
            request.temperature_metric,
        ],
    }
    return (0 if result.ok else 1), response


def _run_one(
    input_json: Path,
    *,
    commit: bool,
    init_schema: bool,
    conn=None,
    capture_logs: bool = False,
    publish_wake: bool = True,
    schema_ready: bool = False,
    writer_lock: _WriterLockFactory | None = None,
    deadline_at: datetime | None = None,
) -> tuple[int, str, str]:
    log_output = StringIO()
    handler: logging.Handler | None = None
    if capture_logs:
        handler = logging.StreamHandler(log_output)
        handler.setLevel(logging.WARNING)
        logging.getLogger().addHandler(handler)
    stage_receipt = _StageReceipt(input_json, deadline_at)
    stage_receipt.mark("open_read_snapshot")
    try:
        if conn is None:
            returncode, response = _materialize(
                input_json,
                commit=commit,
                init_schema=init_schema,
                conn=None,
                publish_wake=publish_wake,
                schema_ready=schema_ready,
                writer_lock=writer_lock,
                stage_receipt=stage_receipt,
            )
        else:
            with stage_receipt.sqlite_deadline_guard(conn):
                returncode, response = _materialize(
                    input_json,
                    commit=commit,
                    init_schema=init_schema,
                    conn=conn,
                    publish_wake=publish_wake,
                    schema_ready=schema_ready,
                    writer_lock=writer_lock,
                    stage_receipt=stage_receipt,
                )
        encoded = json.dumps(response, sort_keys=True) + "\n"
        if returncode == 2:
            return returncode, "", log_output.getvalue() + encoded
        return returncode, encoded, log_output.getvalue()
    except MaterializationDeadlineExceeded as exc:
        response = {
            "status": "DEFERRED",
            "reason_codes": [str(exc)],
            "stage": exc.stage,
            "deadline_at": exc.deadline_at.astimezone(UTC).isoformat(),
            "committed": False,
            "reactor_wake_published": False,
        }
        return 75, "", log_output.getvalue() + json.dumps(response, sort_keys=True) + "\n"
    except Exception as exc:
        return 2, "", log_output.getvalue() + json.dumps(
            _error_response(exc), sort_keys=True
        ) + "\n"
    finally:
        if handler is not None:
            logging.getLogger().removeHandler(handler)


def _print_batch_envelope(
    input_json: Path,
    returncode: int,
    stdout: str,
    stderr: str,
) -> None:
    print(
        json.dumps(
            {
                "input_json": str(input_json),
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
            },
            sort_keys=True,
        ),
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize replacement forecast live posterior"
    )
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--input-json", type=Path, help="Materialization request JSON")
    inputs.add_argument(
        "--batch-input-json",
        type=Path,
        nargs="+",
        help="Materialization requests processed in one process with per-request transactions",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit DB writes; default is dry-run rollback",
    )
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="Idempotently initialize forecast/readiness tables before materializing",
    )
    parser.add_argument(
        "--deadline-utc",
        help="Queue-owned absolute child deadline; omitted for operator dry-runs.",
    )
    parser.add_argument("--print-template", action="store_true")
    args = parser.parse_args(argv)
    if args.print_template:
        print(json.dumps(_template(), sort_keys=True, indent=2))
        return 0
    if args.input_json is None and not args.batch_input_json:
        parser.error(
            "--input-json or --batch-input-json is required unless --print-template is set"
        )
    deadline_at = (
        None
        if args.deadline_utc in (None, "")
        else _dt(str(args.deadline_utc), field_name="deadline_utc")
    )
    if args.batch_input_json:
        from src.state.db import (
            connect_existing_forecasts_db_without_journal_bootstrap,
            get_forecasts_connection,
        )

        conn = (
            connect_existing_forecasts_db_without_journal_bootstrap()
            if args.commit
            else get_forecasts_connection(write_class=None)
        )
        try:
            _attach_world_read_only(conn)
            schema_ready = False
            if args.commit:
                schema_receipt = _StageReceipt(args.batch_input_json[0], deadline_at)
                schema_receipt.mark("write_verify")
                try:
                    with schema_receipt.sqlite_deadline_guard(conn):
                        schema_receipt.require_budget()
                        receipt = _prepare_live_schema_and_manifest(
                            conn,
                            init_schema=args.init_schema,
                            schema_ready=False,
                            openmeteo_manifest=None,
                            anchor_artifact_id=None,
                            writer_lock=_forecast_writer_lock,
                        )
                    schema_ready = receipt.schema_ready
                except MaterializationDeadlineExceeded as exc:
                    stderr = json.dumps(
                        {
                            "status": "DEFERRED",
                            "reason_codes": [str(exc)],
                            "stage": exc.stage,
                            "deadline_at": exc.deadline_at.astimezone(UTC).isoformat(),
                        },
                        sort_keys=True,
                    ) + "\n"
                    for input_json in args.batch_input_json:
                        _print_batch_envelope(input_json, 75, "", stderr)
                    return 0
                except sqlite3.OperationalError as exc:
                    if schema_receipt.deadline_expired():
                        deadline_exc = MaterializationDeadlineExceeded(
                            schema_receipt.stage,
                            schema_receipt.deadline_at,
                        )
                        stderr = json.dumps(
                            {
                                "status": "DEFERRED",
                                "reason_codes": [str(deadline_exc)],
                                "stage": deadline_exc.stage,
                                "deadline_at": deadline_exc.deadline_at.astimezone(
                                    UTC
                                ).isoformat(),
                            },
                            sort_keys=True,
                        ) + "\n"
                    else:
                        stderr = json.dumps(_error_response(exc), sort_keys=True) + "\n"
                    for input_json in args.batch_input_json:
                        _print_batch_envelope(
                            input_json,
                            75 if schema_receipt.deadline_expired() else 2,
                            "",
                            stderr,
                        )
                    return 0
                except Exception as exc:
                    stderr = json.dumps(_error_response(exc), sort_keys=True) + "\n"
                    for input_json in args.batch_input_json:
                        _print_batch_envelope(input_json, 2, "", stderr)
                    return 0
            for input_json in args.batch_input_json:
                returncode, stdout, stderr = _run_one(
                    input_json,
                    commit=args.commit,
                    init_schema=False,
                    conn=conn,
                    capture_logs=True,
                    publish_wake=True,
                    schema_ready=schema_ready,
                    writer_lock=_forecast_writer_lock,
                    deadline_at=deadline_at,
                )
                _print_batch_envelope(input_json, returncode, stdout, stderr)
        finally:
            conn.close()
        return 0
    returncode, stdout, stderr = _run_one(
        args.input_json,
        commit=args.commit,
        init_schema=args.init_schema,
        deadline_at=deadline_at,
    )
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
