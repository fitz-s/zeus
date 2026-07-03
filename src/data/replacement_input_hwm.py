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
posterior is stale and must not be served for a live trade decision.

``event_reactor_adapter.py`` keeps thin delegating wrappers with identical
names and signatures (``family=...``) so its existing call sites and tests
(``tests/test_live_safety_invariants.py:4356,:4417``) stay byte-identical.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.data.market_topology_rows import _table_columns, _table_exists

UTC = timezone.utc


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
    decision_iso = decision_time.astimezone(UTC).isoformat()
    params: list[object] = [
        city,
        target_date,
        metric,
        decision_iso,
        decision_iso,
    ]
    if "source_id" in columns:
        predicates.append("source_id = 'openmeteo_ecmwf_ifs_9km'")
    try:
        row = conn.execute(
            f"""
            SELECT source_cycle_time
              FROM {table_ref}
             WHERE {' AND '.join(predicates)}
               AND datetime(source_cycle_time) <= datetime(?)
             GROUP BY source_cycle_time
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
) -> str | None:
    posterior_cycle = _parse_source_cycle_utc(posterior_source_cycle_time)
    if posterior_cycle is None:
        return f"posterior_source_cycle_unparseable={posterior_source_cycle_time!s}"
    latest_raw_cycle, basis = latest_live_input_cycle(
        conn, city=city, target_date=target_date, metric=metric, decision_time=decision_time
    )
    if latest_raw_cycle is None or latest_raw_cycle <= posterior_cycle:
        return None
    lag_hours = (latest_raw_cycle - posterior_cycle).total_seconds() / 3600.0
    return (
        f"basis={basis or 'source_cycle_time_live_input_lag'}:"
        f"latest_raw_cycle={latest_raw_cycle.isoformat()}:"
        f"posterior_cycle={posterior_cycle.isoformat()}:"
        f"lag_h={lag_hours:.2f}"
    )
