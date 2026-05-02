# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b market-topology readiness contract.
"""Repository helpers for current market topology snapshots."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import date, datetime
from typing import Any, Iterator

TOPOLOGY_STATUSES = frozenset({"CURRENT", "STALE", "EMPTY_FALLBACK", "MISMATCH", "UNKNOWN"})
SOURCE_CONTRACT_STATUSES = frozenset({"MATCH", "MISMATCH", "UNKNOWN", "QUARANTINED"})
AUTHORITY_STATUSES = frozenset({"VERIFIED", "STALE", "EMPTY_FALLBACK", "UNKNOWN"})


def _to_iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _json_text(value: Any, *, default: object) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        json.loads(value)
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _scope_key(*parts: object) -> str:
    return "|".join("" if part is None else str(part) for part in parts)


@contextlib.contextmanager
def _savepoint(conn: sqlite3.Connection, name: str) -> Iterator[None]:
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
        conn.execute(f"RELEASE SAVEPOINT {name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        conn.execute(f"RELEASE SAVEPOINT {name}")
        raise


def write_market_topology_state(
    conn: sqlite3.Connection,
    *,
    topology_id: str,
    market_family: str,
    condition_id: str,
    status: str,
    source_contract_status: str,
    authority_status: str,
    event_id: str | None = None,
    question_id: str | None = None,
    city_id: str | None = None,
    city_timezone: str | None = None,
    target_local_date: date | str | None = None,
    temperature_metric: str | None = None,
    physical_quantity: str | None = None,
    observation_field: str | None = None,
    data_version: str | None = None,
    token_ids_json: Any = None,
    bin_topology_hash: str | None = None,
    gamma_captured_at: datetime | str | None = None,
    gamma_updated_at: datetime | str | None = None,
    source_contract_reason: str | None = None,
    expires_at: datetime | str | None = None,
    provenance_json: Any = None,
) -> None:
    if status not in TOPOLOGY_STATUSES:
        raise ValueError(f"invalid topology status: {status}")
    if source_contract_status not in SOURCE_CONTRACT_STATUSES:
        raise ValueError(f"invalid source_contract_status: {source_contract_status}")
    if authority_status not in AUTHORITY_STATUSES:
        raise ValueError(f"invalid authority_status: {authority_status}")
    target_local_date_iso = _to_iso(target_local_date)
    scope_key = _scope_key(
        market_family,
        condition_id,
        city_id,
        target_local_date_iso,
        temperature_metric,
        data_version,
    )
    with _savepoint(conn, "market_topology_write"):
        conn.execute(
            """
            INSERT INTO market_topology_state (
                topology_id, scope_key, market_family, event_id, condition_id, question_id,
                city_id, city_timezone, target_local_date, temperature_metric,
                physical_quantity, observation_field, data_version, token_ids_json,
                bin_topology_hash, gamma_captured_at, gamma_updated_at,
                source_contract_status, source_contract_reason, authority_status,
                status, expires_at, provenance_json
            ) VALUES (
                :topology_id, :scope_key, :market_family, :event_id, :condition_id, :question_id,
                :city_id, :city_timezone, :target_local_date, :temperature_metric,
                :physical_quantity, :observation_field, :data_version, :token_ids_json,
                :bin_topology_hash, :gamma_captured_at, :gamma_updated_at,
                :source_contract_status, :source_contract_reason, :authority_status,
                :status, :expires_at, :provenance_json
            )
            ON CONFLICT(scope_key) DO UPDATE SET
                topology_id = excluded.topology_id,
                event_id = excluded.event_id,
                question_id = excluded.question_id,
                city_timezone = excluded.city_timezone,
                physical_quantity = excluded.physical_quantity,
                observation_field = excluded.observation_field,
                token_ids_json = excluded.token_ids_json,
                bin_topology_hash = excluded.bin_topology_hash,
                gamma_captured_at = excluded.gamma_captured_at,
                gamma_updated_at = excluded.gamma_updated_at,
                source_contract_status = excluded.source_contract_status,
                source_contract_reason = excluded.source_contract_reason,
                authority_status = excluded.authority_status,
                status = excluded.status,
                expires_at = excluded.expires_at,
                provenance_json = excluded.provenance_json
            """,
            {
                "topology_id": topology_id,
                "scope_key": scope_key,
                "market_family": market_family,
                "event_id": event_id,
                "condition_id": condition_id,
                "question_id": question_id,
                "city_id": city_id,
                "city_timezone": city_timezone,
                "target_local_date": target_local_date_iso,
                "temperature_metric": temperature_metric,
                "physical_quantity": physical_quantity,
                "observation_field": observation_field,
                "data_version": data_version,
                "token_ids_json": _json_text(token_ids_json, default=[]),
                "bin_topology_hash": bin_topology_hash,
                "gamma_captured_at": _to_iso(gamma_captured_at),
                "gamma_updated_at": _to_iso(gamma_updated_at),
                "source_contract_status": source_contract_status,
                "source_contract_reason": source_contract_reason,
                "authority_status": authority_status,
                "status": status,
                "expires_at": _to_iso(expires_at),
                "provenance_json": _json_text(provenance_json, default={}),
            },
        )


def get_market_topology_state(conn: sqlite3.Connection, topology_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM market_topology_state WHERE topology_id = ?", (topology_id,)).fetchone()
    return dict(row) if row else None


def get_current_market_topology(
    conn: sqlite3.Connection,
    *,
    market_family: str,
    condition_id: str,
    city_id: str | None = None,
    target_local_date: date | str | None = None,
    temperature_metric: str | None = None,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM market_topology_state
        WHERE market_family = ?
          AND condition_id = ?
          AND (? IS NULL OR city_id = ?)
          AND (? IS NULL OR target_local_date = ?)
          AND (? IS NULL OR temperature_metric = ?)
        ORDER BY recorded_at DESC
        LIMIT 1
        """,
        (
            market_family,
            condition_id,
            city_id,
            city_id,
            _to_iso(target_local_date),
            _to_iso(target_local_date),
            temperature_metric,
            temperature_metric,
        ),
    ).fetchone()
    return dict(row) if row else None
