# Lifecycle: created=2026-04-25; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Canonical daily observations row mapping and revision-preserving backfill writes.
# Reuse: Use for `observations` high/low daily rows only; do not use for obs_v2 hourly instants.
"""Canonical daily observation writer helpers.

The live appender couples current-row writes with `data_coverage`; backfills do
not own coverage, but they must use the same canonical `observations` row
shape. This module keeps the row mapping shared and provides a hash-checked
revision path for packet-approved backfills.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

from src.types.observation_atom import ObservationAtom


INSERTED = "inserted"
NOOP = "noop"
REVISION = "revision"

DAILY_OBSERVATION_COLUMNS: tuple[str, ...] = (
    "city",
    "target_date",
    "source",
    "high_temp",
    "low_temp",
    "unit",
    "station_id",
    "fetched_at",
    "high_raw_value",
    "high_raw_unit",
    "high_target_unit",
    "low_raw_value",
    "low_raw_unit",
    "low_target_unit",
    "high_fetch_utc",
    "high_local_time",
    "high_collection_window_start_utc",
    "high_collection_window_end_utc",
    "low_fetch_utc",
    "low_local_time",
    "low_collection_window_start_utc",
    "low_collection_window_end_utc",
    "timezone",
    "utc_offset_minutes",
    "dst_active",
    "is_ambiguous_local_hour",
    "is_missing_local_hour",
    "hemisphere",
    "season",
    "month",
    "rebuild_run_id",
    "data_source_version",
    "authority",
    "high_provenance_metadata",
    "low_provenance_metadata",
)
_KEY_COLUMNS: tuple[str, ...] = ("city", "target_date", "source")
_REVISION_REASON = "payload_hash_mismatch"

_INSERT_SQL = f"""
    INSERT INTO observations ({", ".join(DAILY_OBSERVATION_COLUMNS)})
    VALUES ({", ".join("?" for _ in DAILY_OBSERVATION_COLUMNS)})
"""
_UPSERT_SQL = (
    _INSERT_SQL
    + """
    ON CONFLICT(city, target_date, source) DO UPDATE SET
"""
    + ",\n".join(
        f"        {column} = excluded.{column}"
        for column in DAILY_OBSERVATION_COLUMNS
        if column not in _KEY_COLUMNS
    )
)
_SELECT_EXISTING_SQL = f"""
    SELECT id, {", ".join(DAILY_OBSERVATION_COLUMNS)}
    FROM observations
    WHERE city = ? AND target_date = ? AND source = ?
"""


def _json_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _json_loads(payload: Any) -> Any:
    if not isinstance(payload, str):
        return payload
    try:
        return json.loads(payload)
    except ValueError:
        return payload


def _row_from_cursor(cursor: sqlite3.Cursor, row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    columns = [description[0] for description in cursor.description]
    if isinstance(row, sqlite3.Row):
        return {column: row[column] for column in columns}
    return dict(zip(columns, row))


def _clean_hash(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _payload_hash_from_metadata(metadata_json: Any, *, value_type: str) -> str | None:
    metadata = _json_loads(metadata_json)
    if not isinstance(metadata, Mapping):
        return None
    component_hashes = metadata.get("component_payload_hashes")
    if isinstance(component_hashes, Mapping):
        component_keys = (
            ("high", "CLMMAXT")
            if value_type == "high"
            else ("low", "CLMMINT")
        )
        for key in component_keys:
            payload_hash = _clean_hash(component_hashes.get(key))
            if payload_hash is not None:
                return payload_hash
    return _clean_hash(metadata.get("payload_hash"))


def _combine_hashes(high_hash: str, low_hash: str) -> str:
    if high_hash == low_hash:
        return high_hash
    return (
        "sha256:"
        + hashlib.sha256(f"{high_hash}\n{low_hash}".encode("utf-8")).hexdigest()
    )


def _daily_payload_hashes(row: Mapping[str, Any]) -> dict[str, str | None]:
    high_hash = _payload_hash_from_metadata(
        row.get("high_provenance_metadata"),
        value_type="high",
    )
    low_hash = _payload_hash_from_metadata(
        row.get("low_provenance_metadata"),
        value_type="low",
    )
    combined_hash = (
        _combine_hashes(high_hash, low_hash)
        if high_hash is not None and low_hash is not None
        else None
    )
    return {
        "combined": combined_hash,
        "high": high_hash,
        "low": low_hash,
    }


def _require_incoming_payload_hashes(row: Mapping[str, Any]) -> dict[str, str]:
    hashes = _daily_payload_hashes(row)
    missing = [name for name, value in hashes.items() if value is None]
    if missing:
        raise ValueError(
            "daily observation incoming row is missing payload identity: "
            + ", ".join(missing)
        )
    return {
        "combined": str(hashes["combined"]),
        "high": str(hashes["high"]),
        "low": str(hashes["low"]),
    }


def observation_row_from_atoms(
    atom_high: ObservationAtom,
    atom_low: ObservationAtom,
) -> dict[str, Any]:
    """Build the canonical `observations` row for one high/low daily pair."""
    assert atom_high.value_type == "high"
    assert atom_low.value_type == "low"
    assert atom_high.city == atom_low.city
    assert atom_high.target_date == atom_low.target_date
    assert atom_high.source == atom_low.source
    assert atom_high.target_unit == atom_low.target_unit

    return {
        "city": atom_high.city,
        "target_date": atom_high.target_date.isoformat(),
        "source": atom_high.source,
        "high_temp": atom_high.value,
        "low_temp": atom_low.value,
        "unit": atom_high.target_unit,
        "station_id": atom_high.station_id,
        "fetched_at": atom_high.fetch_utc.isoformat(),
        "high_raw_value": atom_high.raw_value,
        "high_raw_unit": atom_high.raw_unit,
        "high_target_unit": atom_high.target_unit,
        "low_raw_value": atom_low.raw_value,
        "low_raw_unit": atom_low.raw_unit,
        "low_target_unit": atom_low.target_unit,
        "high_fetch_utc": atom_high.fetch_utc.isoformat(),
        "high_local_time": atom_high.local_time.isoformat(),
        "high_collection_window_start_utc": atom_high.collection_window_start_utc.isoformat(),
        "high_collection_window_end_utc": atom_high.collection_window_end_utc.isoformat(),
        "low_fetch_utc": atom_low.fetch_utc.isoformat(),
        "low_local_time": atom_low.local_time.isoformat(),
        "low_collection_window_start_utc": atom_low.collection_window_start_utc.isoformat(),
        "low_collection_window_end_utc": atom_low.collection_window_end_utc.isoformat(),
        "timezone": atom_high.timezone,
        "utc_offset_minutes": atom_high.utc_offset_minutes,
        "dst_active": int(atom_high.dst_active),
        "is_ambiguous_local_hour": int(atom_high.is_ambiguous_local_hour),
        "is_missing_local_hour": int(atom_high.is_missing_local_hour),
        "hemisphere": atom_high.hemisphere,
        "season": atom_high.season,
        "month": atom_high.month,
        "rebuild_run_id": atom_high.rebuild_run_id,
        "data_source_version": atom_high.data_source_version,
        "authority": atom_high.authority,
        "high_provenance_metadata": json.dumps(atom_high.provenance_metadata),
        "low_provenance_metadata": json.dumps(atom_low.provenance_metadata),
    }


def _values_from_row(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row[column] for column in DAILY_OBSERVATION_COLUMNS)


def insert_or_update_current_observation(
    conn: sqlite3.Connection,
    atom_high: ObservationAtom,
    atom_low: ObservationAtom,
) -> None:
    """Write the current daily observation row using live UPSERT semantics."""
    row = observation_row_from_atoms(atom_high, atom_low)
    conn.execute(_UPSERT_SQL, _values_from_row(row))


def _insert_daily_revision(
    conn: sqlite3.Connection,
    *,
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
    existing_hashes: Mapping[str, str | None],
    incoming_hashes: Mapping[str, str],
    reason: str,
    writer: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO daily_observation_revisions (
            city, target_date, source, natural_key_json,
            existing_row_id, existing_combined_payload_hash,
            incoming_combined_payload_hash, existing_high_payload_hash,
            existing_low_payload_hash, incoming_high_payload_hash,
            incoming_low_payload_hash, reason, writer,
            existing_row_json, incoming_row_json
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?
        )
        """,
        (
            incoming["city"],
            incoming["target_date"],
            incoming["source"],
            _json_dumps({column: incoming[column] for column in _KEY_COLUMNS}),
            existing.get("id"),
            existing_hashes.get("combined"),
            incoming_hashes["combined"],
            existing_hashes.get("high"),
            existing_hashes.get("low"),
            incoming_hashes["high"],
            incoming_hashes["low"],
            reason,
            writer,
            _json_dumps(dict(existing)),
            _json_dumps(dict(incoming)),
        ),
    )


def write_daily_observation_with_revision(
    conn: sqlite3.Connection,
    atom_high: ObservationAtom,
    atom_low: ObservationAtom,
    *,
    writer: str,
) -> str:
    """Write a daily observation row without overwriting disputed evidence."""
    incoming = observation_row_from_atoms(atom_high, atom_low)
    incoming_hashes = _require_incoming_payload_hashes(incoming)

    savepoint = f"sp_daily_observation_write_{id(incoming)}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        cursor = conn.execute(
            _SELECT_EXISTING_SQL,
            (incoming["city"], incoming["target_date"], incoming["source"]),
        )
        existing_row = cursor.fetchone()
        if existing_row is None:
            conn.execute(_INSERT_SQL, _values_from_row(incoming))
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            return INSERTED

        existing = _row_from_cursor(cursor, existing_row)
        existing_hashes = _daily_payload_hashes(existing)
        if existing_hashes["combined"] == incoming_hashes["combined"]:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            return NOOP

        reason = (
            "missing_existing_payload_hash"
            if existing_hashes["combined"] is None
            else _REVISION_REASON
        )
        _insert_daily_revision(
            conn,
            existing=existing,
            incoming=incoming,
            existing_hashes=existing_hashes,
            incoming_hashes=incoming_hashes,
            reason=reason,
            writer=writer,
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        return REVISION
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
