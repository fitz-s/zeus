# Created: 2026-07-02
# Last reused/audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   section 1 row "q_version + input HWMs (A1)".
"""Shared read-time raw-input high-water-mark (HWM) lag check.

Moved out of ``src/engine/event_reactor_adapter.py`` (W0.1, 2026-07-02) so read
paths other than the no-submit-cert path can enforce the SAME fail-closed
raw-input tripwire without a private cross-module import. Compares the latest
raw ``raw_model_forecasts`` / ``raw_forecast_artifacts`` ``source_cycle_time``
available by ``decision_time`` against a served posterior's
``source_cycle_time``; a newer raw input than the posterior means the
posterior is stale and must not be served for a live trade decision. For
used-model rows from the same cycle, a raw capture/available timestamp newer
than the posterior ``computed_at`` is also stale: the posterior did not see the
latest executable row for its own model family.

``event_reactor_adapter.py`` keeps thin delegating wrappers with identical
names and signatures (``family=...``) so its existing call sites and tests
(``tests/test_live_safety_invariants.py:4356,:4417``) stay byte-identical.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

from src.data.market_topology_rows import _table_columns, _table_exists

UTC = timezone.utc
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _FrozenInputHwm:
    conn: sqlite3.Connection
    decision_iso: str
    requests: frozenset[tuple[str, str, str]]
    artifact_loaded: bool
    artifact_cycles: Mapping[tuple[str, str, str], datetime]


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
        attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" in attached:
            exists = conn.execute(
                "SELECT 1 FROM forecasts.sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            if exists is not None:
                return f"forecasts.{table_name}"
        if "world" in attached:
            exists = conn.execute(
                "SELECT 1 FROM world.sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            if exists is not None:
                return f"world.{table_name}"
    except Exception:  # noqa: BLE001 - live gate must fail closed at the caller
        pass
    if _table_exists(conn, table_name):
        return table_name
    return None


def _table_ref_columns(conn: sqlite3.Connection, table_ref: str) -> set[str]:
    if "." in table_ref:
        schema, table = table_ref.split(".", 1)
        return {row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()}
    return _table_columns(conn, table_ref)


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
    columns = _table_ref_columns(conn, table_ref)
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
    except Exception:  # noqa: BLE001 - live gate must fail closed at the caller
        return None
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
        and frozen.conn is conn
        and frozen.decision_iso == decision_iso
        and frozen.artifact_loaded
        and key in frozen.requests
    ):
        return frozen.artifact_cycles.get(key)
    table_ref = _authority_table_ref(conn, "raw_forecast_artifacts")
    if table_ref is None:
        return None
    columns = _table_ref_columns(conn, table_ref)
    required = {
        "source_cycle_time",
        "captured_at",
        "source_available_at",
        "artifact_metadata_json",
    }
    if not required.issubset(columns):
        return None
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
        predicates.append("source_id = 'openmeteo_ecmwf_ifs_9km'")
    can_verify_payload = "artifact_path" in columns
    select_payload = ", artifact_path" if can_verify_payload else ""
    if conn.in_transaction:
        try:
            data_version_row = conn.execute("PRAGMA data_version").fetchone()
            data_version = int(data_version_row[0]) if data_version_row is not None else -1
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
        except Exception:  # noqa: BLE001 - live gate must fail closed at the caller
            return None
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
    except Exception:  # noqa: BLE001 - live gate must fail closed at the caller
        return None
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
        predicates.append("source_id = 'openmeteo_ecmwf_ifs_9km'")
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
        if not all(key) or key in cycles:
            continue
        if metadata_type != "object":
            continue
        if "artifact_path" in columns:
            if payload_path_type in {"", "null", "text"}:
                metadata = (
                    {"openmeteo_payload_json": payload_path}
                    if payload_path_type == "text"
                    else {}
                )
            else:
                try:
                    metadata = json.loads(str(metadata_raw or "{}"))
                except (TypeError, ValueError):
                    continue
                if not isinstance(metadata, dict):
                    continue
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


def _batch_artifact_cycles(
    conn: sqlite3.Connection,
    *,
    requests: frozenset[tuple[str, str, str]],
    decision_iso: str,
) -> tuple[bool, dict[tuple[str, str, str], datetime]]:
    table_ref = _authority_table_ref(conn, "raw_forecast_artifacts")
    if table_ref is None:
        return True, {}
    columns = frozenset(_table_ref_columns(conn, table_ref))
    required = {
        "source_cycle_time",
        "captured_at",
        "source_available_at",
        "artifact_metadata_json",
    }
    if not required.issubset(columns):
        return True, {}
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
            (*[value for key in chunk for value in key], decision_iso, decision_iso, decision_iso),
        ).fetchall()
        cycles.update(_artifact_cycles_from_rows(rows, columns=columns))
    return True, cycles


def prime_frozen_replacement_artifact_hwm(
    conn: sqlite3.Connection,
    *,
    requests: Iterable[tuple[str, str, str]],
    decision_time: datetime,
) -> Callable[[], None]:
    """Prime artifact HWMs for one explicitly owned read transaction."""

    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        return lambda: None
    normalized = frozenset(
        (str(city), str(target_date), str(metric))
        for city, target_date, metric in requests
        if city and target_date and metric
    )
    if not normalized:
        return lambda: None
    decision_iso = decision_time.astimezone(UTC).isoformat()
    artifact_loaded = False
    artifact_cycles: dict[tuple[str, str, str], datetime] = {}
    try:
        artifact_loaded, artifact_cycles = _batch_artifact_cycles(
            conn,
            requests=normalized,
            decision_iso=decision_iso,
        )
    except Exception as exc:  # noqa: BLE001 - scalar fail-closed fallback remains authoritative
        _LOG.warning("frozen artifact HWM prime failed; using scalar reads: %s", exc)

    token = _FROZEN_INPUT_HWM.set(
        _FrozenInputHwm(
            conn=conn,
            decision_iso=decision_iso,
            requests=normalized,
            artifact_loaded=artifact_loaded,
            artifact_cycles=artifact_cycles,
        )
    )
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        _FROZEN_INPUT_HWM.reset(token)

    return release


def _posterior_provenance_for_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: object,
    metric: str,
    posterior_source_cycle_time: object,
) -> dict[str, object]:
    table_ref = _authority_table_ref(conn, "forecast_posteriors")
    if table_ref is None:
        return {}
    columns = _table_ref_columns(conn, table_ref)
    required = {"city", "target_date", "temperature_metric", "source_cycle_time", "provenance_json"}
    if not required.issubset(columns):
        return {}
    order_terms = []
    if "computed_at" in columns:
        order_terms.append("datetime(computed_at) DESC")
    if "posterior_id" in columns:
        order_terms.append("posterior_id DESC")
    order_sql = ", ".join(order_terms) if order_terms else "rowid DESC"
    try:
        row = conn.execute(
            f"""
            SELECT provenance_json
              FROM {table_ref}
             WHERE city = ?
               AND target_date = ?
               AND temperature_metric = ?
               AND datetime(source_cycle_time) = datetime(?)
             ORDER BY {order_sql}
             LIMIT 1
            """,
            (city, target_date, metric, str(posterior_source_cycle_time)),
        ).fetchone()
    except Exception:  # noqa: BLE001 - live gate must fail closed at the caller
        return {}
    if row is None:
        return {}
    try:
        raw = row["provenance_json"]
    except Exception:  # noqa: BLE001
        raw = row[0]
    try:
        provenance = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return provenance if isinstance(provenance, dict) else {}


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
    candidates: list[object] = []
    candidates.append(provenance.get("used_models"))
    fusion = provenance.get("bayes_precision_fusion")
    if isinstance(fusion, dict):
        candidates.append(fusion.get("used_models"))
        source_clock = fusion.get("source_clock_one_scheme")
        if isinstance(source_clock, dict):
            candidates.append(source_clock.get("used_weights"))
            candidates.append(source_clock.get("configured_sources"))
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
    return frozenset(models)


def _provenance_has_current_value_serving(
    provenance: Mapping[str, object],
) -> bool:
    fusion = provenance.get("bayes_precision_fusion")
    if not isinstance(fusion, dict):
        return False
    serving = fusion.get("current_value_serving")
    return isinstance(serving, dict) and bool(serving)


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
    columns = _table_ref_columns(conn, table_ref)
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
    except Exception:  # noqa: BLE001 - live gate must fail closed at the caller
        return None
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
) -> str | None:
    posterior_cycle = _parse_source_cycle_utc(posterior_source_cycle_time)
    if posterior_cycle is None:
        return f"posterior_source_cycle_unparseable={posterior_source_cycle_time!s}"
    posterior_computed = _parse_source_cycle_utc(posterior_computed_at)
    provenance = (
        posterior_provenance
        if posterior_provenance is not None
        else _posterior_provenance_for_cycle(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            posterior_source_cycle_time=posterior_source_cycle_time,
        )
    )
    rich_used_input_provenance = _provenance_has_current_value_serving(provenance)
    used_raw_mark = latest_used_raw_model_input_mark(
        conn,
        city=city,
        target_date=target_date,
        metric=metric,
        decision_time=decision_time,
        posterior_source_cycle_time=posterior_source_cycle_time,
        posterior_provenance=provenance,
    )
    if (
        rich_used_input_provenance
        and used_raw_mark is not None
        and posterior_computed is not None
        and used_raw_mark[1] is not None
        and used_raw_mark[1] > posterior_computed
    ):
        lag_seconds = (used_raw_mark[1] - posterior_computed).total_seconds()
        return (
            "basis=used_raw_model_forecasts_late_input:"
            f"latest_raw_cycle={used_raw_mark[0].isoformat()}:"
            f"posterior_cycle={posterior_cycle.isoformat()}:"
            f"latest_raw_input_at={used_raw_mark[1].isoformat()}:"
            f"posterior_computed_at={posterior_computed.isoformat()}:"
            f"lag_s={lag_seconds:.0f}"
        )
    candidates = [
        (
            latest_raw_artifact_input_cycle(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                decision_time=decision_time,
            ),
            "source_cycle_time_raw_forecast_artifacts_lag",
        ),
    ]
    if not rich_used_input_provenance:
        candidates.extend(
            (
                (
                    latest_raw_model_input_cycle(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        decision_time=decision_time,
                    ),
                    "source_cycle_time_raw_model_forecasts_lag",
                ),
                (
                    used_raw_mark[0] if used_raw_mark is not None else None,
                    "source_cycle_time_used_raw_model_forecasts_lag",
                ),
            )
        )
    candidates = [(cycle, basis) for cycle, basis in candidates if cycle is not None]
    if not candidates:
        return None
    latest_raw_cycle, basis = max(candidates, key=lambda item: item[0])
    if latest_raw_cycle is None or latest_raw_cycle <= posterior_cycle:
        if (
            used_raw_mark is not None
            and posterior_computed is not None
            and used_raw_mark[0] == posterior_cycle
            and used_raw_mark[1] is not None
            and used_raw_mark[1] > posterior_computed
        ):
            lag_seconds = (used_raw_mark[1] - posterior_computed).total_seconds()
            return (
                "basis=used_raw_model_forecasts_same_cycle_late_input:"
                f"latest_raw_cycle={used_raw_mark[0].isoformat()}:"
                f"posterior_cycle={posterior_cycle.isoformat()}:"
                f"latest_raw_input_at={used_raw_mark[1].isoformat()}:"
                f"posterior_computed_at={posterior_computed.isoformat()}:"
                f"lag_s={lag_seconds:.0f}"
            )
        return None
    lag_hours = (latest_raw_cycle - posterior_cycle).total_seconds() / 3600.0
    return (
        f"basis={basis or 'source_cycle_time_live_input_lag'}:"
        f"latest_raw_cycle={latest_raw_cycle.isoformat()}:"
        f"posterior_cycle={posterior_cycle.isoformat()}:"
        f"lag_h={lag_hours:.2f}"
    )
