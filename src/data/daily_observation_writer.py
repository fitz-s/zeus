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
from dataclasses import dataclass
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



def _current_wrh_content_identity(snapshot) -> str:
    """Normalized contract-view membership/finality, independent of HTTP metadata."""
    return hashlib.sha256(_json_dumps({
        "revision": snapshot.provenance()["revision"], "product": "weather.gov_wrh_timeseries",
        "station": snapshot.station, "target_date": snapshot.target_date, "unit": snapshot.unit,
        "view": snapshot.view, "timezone": snapshot.timezone_name, "complete_day": snapshot.complete_day,
        "rows": sorted((row.utc.isoformat(), row.air_temp) for row in snapshot.rows
                       if snapshot.view == "all" or row.is_official_report),
    }).encode()).hexdigest()

_WRH_ORDER_RECEIPT_REVISION = "noaa_wrh_acquisition_order_receipt_v1"
_WRH_ORDER_RECEIPT_KEYS = frozenset({
    "revision", "semantic_snapshot_identity", "semantic_content_identity", "scope",
    "retained_semantic_body_sha256", "validated_response_sha256",
    "request_started_at", "received_at", "receipt_identity",
})


@dataclass(frozen=True)
class _WrhAcquisitionOrder:
    request_started_at: Any
    received_at: Any


def _wrh_order_receipt(snapshot, *, semantic_proof, content_identity):
    """Administrative order fence after native replay, never source evidence.

    A byte-distinct NOOP response was checked in memory against the retained
    semantic snapshot. Its digest is an audit value, not a retained-body ref.
    The retained semantic body remains the sole q/membership/finality proof.
    """
    receipt = {
        "revision": _WRH_ORDER_RECEIPT_REVISION,
        "semantic_snapshot_identity": hashlib.sha256(_json_dumps(semantic_proof).encode()).hexdigest(),
        "semantic_content_identity": content_identity,
        "scope": {key: semantic_proof[key] for key in ("city", "target_date", "station", "unit", "view")},
        "retained_semantic_body_sha256": semantic_proof["response_sha256"],
        "validated_response_sha256": snapshot.response_sha256,
        "request_started_at": snapshot.request_started_at.isoformat(),
        "received_at": snapshot.received_at.isoformat(),
    }
    receipt["receipt_identity"] = hashlib.sha256(_json_dumps(receipt).encode()).hexdigest()
    return receipt


def _read_wrh_order_receipt(receipt, *, semantic_proof, content_identity, as_of=None):
    """Validate only bounded control schema, source agreement and causal order."""
    from datetime import datetime
    import re
    if not isinstance(receipt, dict) or set(receipt) != _WRH_ORDER_RECEIPT_KEYS:
        raise ValueError("WRH_ACQUISITION_CONTROL_SCHEMA_INVALID")
    identity = {key: value for key, value in receipt.items() if key != "receipt_identity"}
    if (receipt["revision"] != _WRH_ORDER_RECEIPT_REVISION
            or receipt["receipt_identity"] != hashlib.sha256(_json_dumps(identity).encode()).hexdigest()
            or receipt["semantic_snapshot_identity"] != hashlib.sha256(_json_dumps(semantic_proof).encode()).hexdigest()
            or receipt["semantic_content_identity"] != content_identity
            or receipt["scope"] != {key: semantic_proof[key] for key in ("city", "target_date", "station", "unit", "view")}
            or receipt["retained_semantic_body_sha256"] != semantic_proof["response_sha256"]
            or not isinstance(receipt["validated_response_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", receipt["validated_response_sha256"]) is None):
        raise ValueError("WRH_ACQUISITION_CONTROL_BINDING_INVALID")
    requested, received, source_requested, source_received = (
        datetime.fromisoformat(str(value).replace("Z", "+00:00")) for value in (
            receipt["request_started_at"], receipt["received_at"],
            semantic_proof["request_started_at"], semantic_proof["received_at"],
        )
    )
    if (any(value.tzinfo is None for value in (requested, received, source_requested, source_received))
            or not source_requested <= requested <= received or source_received > received
            or (as_of is not None and received > as_of)):
        raise ValueError("WRH_ACQUISITION_CONTROL_CLOCK_INVALID")
    return _WrhAcquisitionOrder(requested, received)


def prepare_current_noaa_wrh_snapshot(conn, *, city, snapshot, atom_high=None, atom_low=None,
                                    as_of):
    """Journal and replace only a validated complete current WRH product.

    Generic/historical disputed writes retain their quarantine-only contract.
    This typed product also represents explicit empty membership without a
    fabricated temperature atom. A newer complete source snapshot may retract
    either extreme; receipt ordering, not MAX/MIN, owns the replacement.
    """
    from src.data.noaa_wrh_timeseries import WrhCurrentSnapshot, replay_current_snapshot, persist_current_snapshot_body

    if conn.in_transaction:
        raise ValueError("WRH_CURRENT_PREPARATION_REQUIRES_NO_WRITE_TRANSACTION")
    if not isinstance(snapshot, WrhCurrentSnapshot):
        raise ValueError("WRH_CURRENT_TYPED_SNAPSHOT_REQUIRED")
    snapshot = replay_current_snapshot(snapshot.provenance(), city=city,
                                       target_date=snapshot.target_date, as_of=as_of,
                                       _native_body=snapshot.native_body)
    high, low = snapshot.extreme("high"), snapshot.extreme("low")
    if (high is None) != (low is None):
        raise ValueError("WRH_CURRENT_EXTREMA_PAIR_INVALID")
    if high is None:
        if atom_high is not None or atom_low is not None:
            raise ValueError("WRH_EMPTY_PRODUCT_HAS_TEMPERATURE_ATOM")
        incoming = dict.fromkeys(DAILY_OBSERVATION_COLUMNS)
        incoming.update(city=city.name, target_date=snapshot.target_date, source=snapshot.source,
                        unit=snapshot.unit, station_id=snapshot.station, authority="VERIFIED",
                        timezone=city.timezone, data_source_version="noaa_wrh_timeseries_v1")
    else:
        incoming = observation_row_from_atoms(atom_high, atom_low)
        if (incoming["city"] != city.name or incoming["target_date"] != snapshot.target_date
                or incoming["source"] != snapshot.source or incoming["station_id"] != snapshot.station
                or incoming["unit"] != snapshot.unit or incoming["authority"] != "VERIFIED"
                or incoming["high_temp"] != high.value or incoming["low_temp"] != low.value):
            raise ValueError("WRH_CURRENT_ATOMS_DISAGREE_WITH_PRODUCT")
    proof = snapshot.provenance()
    # Content/finality identity is independent of retry clocks. The real native
    # body digest remains separate and is never synthesized from temperature.
    content_identity = _current_wrh_content_identity(snapshot)
    identity = hashlib.sha256((content_identity + "|" + snapshot.received_at.isoformat()).encode()).hexdigest()
    provenance = {
        "wrh_snapshot_content_identity": content_identity,
        "upstream": "weather.gov_wrh_timeseries", "station": snapshot.station,
        "settlement_page_view": snapshot.view, "payload_hash": "sha256:" + snapshot.response_sha256,
        "component_payload_hashes": {"high": "sha256:" + identity, "low": "sha256:" + identity},
        "wrh_current_snapshot": proof,
        "high_local_timestamp": high.local_timestamp if high else None,
        "low_local_timestamp": low.local_timestamp if low else None,
    }
    received = snapshot.received_at.isoformat()
    incoming.update(authority="VERIFIED" if snapshot.complete_day and high is not None else "UNVERIFIED",
                    fetched_at=received, high_fetch_utc=received, low_fetch_utc=received,
                    rebuild_run_id="noaa_wrh_current_" + snapshot.response_sha256,
                    high_provenance_metadata=_json_dumps(provenance), low_provenance_metadata=_json_dumps(provenance))
    incoming_hashes = _require_incoming_payload_hashes(incoming)
    # Custody publication and old-proof replay are outside the canonical write
    # lease. A later CAS checks the exact prepared row image before any mutation.
    cursor = conn.execute(_SELECT_EXISTING_SQL, (city.name, snapshot.target_date, snapshot.source))
    raw = cursor.fetchone()
    existing = None if raw is None else _row_from_cursor(cursor, raw)
    if existing is not None and existing.get("authority") in {"DISPUTED", "QUARANTINED"}:
        # This exact row cannot admit the incoming product. Keep the final
        # writer's CAS/disposition, but retain no uncommittable native body.
        # A changed quarantine disposition requires a fresh preparation.
        return PreparedWrhCurrentWrite(snapshot, _json_dumps(incoming), _json_dumps(existing), None, as_of)
    previous_confirmation = None
    custody_recovery_floor = None
    custody_restore_receipt = None
    metadata = _json_loads(existing.get("high_provenance_metadata")) if existing else None
    proofs = []
    control_order = None
    source_body_retained = False
    if isinstance(metadata, dict) and "wrh_current_snapshot" in metadata:
        proofs = [metadata["wrh_current_snapshot"]]
        confirmation = metadata.get("wrh_latest_confirmation")
        if isinstance(confirmation, dict) and confirmation.get("revision") == _WRH_ORDER_RECEIPT_REVISION:
            control_order = _read_wrh_order_receipt(confirmation, semantic_proof=proofs[0],
                                                   content_identity=metadata["wrh_snapshot_content_identity"], as_of=as_of)
        elif confirmation is not None:
            proofs.append(confirmation)
    from src.data.noaa_wrh_timeseries import read_current_snapshot_body
    import zlib
    if any(proof.get("response_sha256") == snapshot.response_sha256 for proof in proofs):
        try:
            read_current_snapshot_body(snapshot.response_sha256)
            source_body_retained = True
        except (OSError, ValueError, zlib.error):
            # Transport restoration clock only; semantic first possession stays
            # unchanged when these exact bytes restore an existing source fact.
            custody_restore_receipt = as_of.isoformat()
            persist_current_snapshot_body(snapshot.native_body)
            source_body_retained = True
    for proof in proofs:
        try:
            previous_body = read_current_snapshot_body(proof["response_sha256"])
        except (OSError, ValueError, zlib.error):
            # Lost custody authorizes no old q. A fresh independent full
            # product can recover only after all recorded possession clocks;
            # this deliberately rejects an older request's late response.
            from datetime import datetime
            clocks = [datetime.fromisoformat(str(value).replace("Z", "+00:00")) for value in
                      (existing["fetched_at"], *(item["received_at"] for item in proofs))]
            if any(value.tzinfo is None or value > as_of for value in clocks):
                raise ValueError("WRH_CURRENT_RECOVERY_CLOCK_INVALID")
            if control_order is not None:
                clocks.append(control_order.received_at)
            custody_recovery_floor = max(clocks)
        else:
            confirmed = replay_current_snapshot(
                proof, city=city, target_date=snapshot.target_date, as_of=as_of,
                _native_body=previous_body,
            )
            if _current_wrh_content_identity(confirmed) != metadata.get("wrh_snapshot_content_identity"):
                raise ValueError("WRH_CURRENT_CONFIRMATION_MEMBERSHIP_MISMATCH")
            previous_confirmation = confirmed
    if control_order is not None:
        previous_confirmation = control_order
    semantic_noop = (custody_recovery_floor is None and isinstance(metadata, dict)
                     and metadata.get("wrh_snapshot_content_identity") == content_identity)
    if not semantic_noop and not source_body_retained:
        from datetime import datetime
        request_floor = previous_confirmation.request_started_at if previous_confirmation else None
        receipt_floor = previous_confirmation.received_at if previous_confirmation else None
        if existing is not None and receipt_floor is None:
            receipt_floor = request_floor = datetime.fromisoformat(str(existing["fetched_at"]).replace("Z", "+00:00"))
        if custody_recovery_floor is not None:
            request_floor = receipt_floor = max(receipt_floor or custody_recovery_floor, custody_recovery_floor)
        if (existing is None or (snapshot.request_started_at > request_floor and snapshot.received_at > receipt_floor)):
            persist_current_snapshot_body(snapshot.native_body)
            source_body_retained = True
    return PreparedWrhCurrentWrite(snapshot, _json_dumps(incoming),
                                   None if existing is None else _json_dumps(existing),
                                   previous_confirmation, as_of, custody_recovery_floor, custody_restore_receipt, source_body_retained)


@dataclass(frozen=True)
class PreparedWrhCurrentWrite:
    snapshot: Any
    incoming_json: str
    expected_current_json: str | None
    previous_confirmation: Any
    as_of: Any
    custody_recovery_floor: Any = None
    custody_restore_receipt: str | None = None
    source_body_retained: bool = False


def write_current_noaa_wrh_snapshot(conn, *, city, snapshot, atom_high=None, atom_low=None,
                                    as_of, prepared=None) -> str:
    """CAS a prepared immutable row and journal under one short transaction.

    SCOPE: one city/day/source row image. DRAIN: existing acquisition retry
    prepares against the latest row after contention/CAS refusal. RESET: an
    unchanged current row image permits the atomic write; no source re-clocking.
    """
    if prepared is None:
        prepared = prepare_current_noaa_wrh_snapshot(
            conn, city=city, snapshot=snapshot, atom_high=atom_high, atom_low=atom_low, as_of=as_of,
        )
    if not isinstance(prepared, PreparedWrhCurrentWrite) or prepared.snapshot != snapshot or prepared.as_of != as_of:
        raise ValueError("WRH_CURRENT_PREPARED_IDENTITY_MISMATCH")
    incoming = json.loads(prepared.incoming_json)
    if incoming["city"] != city.name or incoming["source"] != snapshot.source:
        raise ValueError("WRH_CURRENT_PREPARED_SCOPE_MISMATCH")
    high, low = snapshot.extreme("high"), snapshot.extreme("low")
    metadata = _json_loads(incoming["high_provenance_metadata"])
    expected = {"city": city.name, "target_date": snapshot.target_date,
                "source": snapshot.source, "station_id": snapshot.station, "unit": snapshot.unit,
                "high_temp": high.value if high else None, "low_temp": low.value if low else None,
                "authority": "VERIFIED" if snapshot.complete_day and high is not None else "UNVERIFIED",
                "fetched_at": snapshot.received_at.isoformat(), "high_fetch_utc": snapshot.received_at.isoformat(),
                "low_fetch_utc": snapshot.received_at.isoformat()}
    if (any(incoming.get(key) != value for key, value in expected.items())
            or not isinstance(metadata, dict)
            or metadata != _json_loads(incoming["low_provenance_metadata"])
            or metadata.get("wrh_current_snapshot") != snapshot.provenance()
            or metadata.get("payload_hash") != "sha256:" + snapshot.response_sha256
            or metadata.get("wrh_snapshot_content_identity") != _current_wrh_content_identity(snapshot)):
        raise ValueError("WRH_CURRENT_PREPARED_CONTENT_MISMATCH")
    incoming_hashes = _require_incoming_payload_hashes(incoming)
    content_identity = metadata["wrh_snapshot_content_identity"]
    sp = f"sp_wrh_current_{id(incoming)}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        cursor = conn.execute(_SELECT_EXISTING_SQL, (city.name, snapshot.target_date, snapshot.source))
        row = cursor.fetchone()
        current_image = None if row is None else _json_dumps(_row_from_cursor(cursor, row))
        if current_image != prepared.expected_current_json:
            raise ValueError("WRH_CURRENT_PREPARED_ROW_CHANGED")
        if row is None:
            if not prepared.source_body_retained:
                raise ValueError("WRH_CURRENT_PREPARED_SOURCE_CUSTODY_MISSING")
            conn.execute(_INSERT_SQL, _values_from_row(incoming))
            result = INSERTED
        else:
            from datetime import datetime
            existing = _row_from_cursor(cursor, row)
            if existing.get("authority") in {"DISPUTED", "QUARANTINED"}:
                conn.execute(f"RELEASE SAVEPOINT {sp}")
                return "existing_disputed"
            old_time = datetime.fromisoformat(str(existing["fetched_at"]).replace("Z", "+00:00"))
            if old_time.tzinfo is None:
                raise ValueError("WRH_EXISTING_RECEIPT_INVALID")
            old_hashes = _daily_payload_hashes(existing)
            existing_metadata = _json_loads(existing.get("high_provenance_metadata"))
            previous_confirmation = prepared.previous_confirmation
            request_floor = previous_confirmation.request_started_at if previous_confirmation else old_time
            receipt_floor = previous_confirmation.received_at if previous_confirmation else old_time
            if prepared.custody_recovery_floor is not None:
                request_floor = receipt_floor = max(receipt_floor, prepared.custody_recovery_floor)
            if (prepared.custody_recovery_floor is None and isinstance(existing_metadata, dict)
                    and existing_metadata.get("wrh_snapshot_content_identity") == content_identity):
                if snapshot.request_started_at > request_floor and snapshot.received_at >= receipt_floor:
                    # Acquisition order survives an unchanged semantic state.
                    # Main source clocks/body are not relabelled or renewed.
                    existing_metadata["wrh_latest_confirmation"] = _wrh_order_receipt(
                        snapshot, semantic_proof=existing_metadata["wrh_current_snapshot"],
                        content_identity=content_identity,
                    )
                if prepared.custody_restore_receipt is not None:
                    existing_metadata["wrh_custody_recovery"] = {
                        "recorded_at": prepared.custody_restore_receipt,
                        "restored_body_sha256": snapshot.response_sha256,
                    }
                encoded = _json_dumps(existing_metadata)
                if encoded != existing["high_provenance_metadata"]:
                    conn.execute("UPDATE observations SET high_provenance_metadata=?, low_provenance_metadata=? "
                                 "WHERE city=? AND target_date=? AND source=?",
                                 (encoded, encoded, city.name, snapshot.target_date, snapshot.source))
                result = NOOP
            elif snapshot.received_at <= receipt_floor or snapshot.request_started_at <= request_floor:
                # Equal receipts with different bodies have no temporal order.
                # Do not let a late/archive replay overwrite current truth.
                result = "older_or_ambiguous_receipt"
            else:
                if not prepared.source_body_retained:
                    raise ValueError("WRH_CURRENT_PREPARED_SOURCE_CUSTODY_MISSING")
                _insert_daily_revision(
                    conn, existing=existing, incoming=incoming, existing_hashes=old_hashes,
                    incoming_hashes=incoming_hashes,
                    reason="missing_existing_payload_hash" if old_hashes["combined"] is None else _REVISION_REASON,
                    writer="noaa_wrh_complete_current_product_v1",
                )
                conn.execute(_UPSERT_SQL, _values_from_row(incoming))
                result = REVISION
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        return result
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise


def read_current_noaa_wrh_snapshot(conn, *, city, target_date: str, as_of, _canonical_owner=False):
    """(owned, snapshot) for current q and exact exits; owned+None blocks reuse.

    An explicit empty snapshot remains a real snapshot with absent extrema.
    SCOPE: this exact NOAA product contract. DRAIN: normal current-product
    acquisition retries. RESET: a complete, valid, causally possessed snapshot.
    Unknown/malformed claimed truth cannot resurrect older print/event maxima.
    """
    from contextlib import closing
    from datetime import datetime
    from pathlib import Path
    import time
    from src.data.noaa_wrh_timeseries import replay_current_snapshot

    source = f"noaa_wrh_{city.wu_station.lower()}"
    claimed = None
    try:
        databases = {str(row[1]): str(row[2]) for row in conn.execute("PRAGMA database_list")}
        if "forecasts" in databases:
            table = "forecasts.observations"
        elif (_canonical_owner or Path(databases.get("main", "")).name == "zeus-forecasts.db"
              or (not databases.get("main") and conn.execute(
                  "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'"
              ).fetchone() is not None)):
            table = "observations"
        else:
            from src.state.db import get_forecasts_connection_with_world_read_only
            deadline = time.monotonic() + 0.05
            with get_forecasts_connection_with_world_read_only(deadline_monotonic=deadline) as owner:
                owner.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                return read_current_noaa_wrh_snapshot(owner, city=city, target_date=target_date, as_of=as_of, _canonical_owner=True)
        revisions = "world.daily_observation_revisions" if "world" in databases else "daily_observation_revisions"
        cursor = conn.execute(f"SELECT * FROM {table} WHERE city=? AND target_date=? AND source=?",
                              (city.name, target_date, source))
        raw = cursor.fetchone()
        if raw is None:
            return False, None
        row = _row_from_cursor(cursor, raw)
        claimed = str(row.get("rebuild_run_id") or "").startswith("noaa_wrh_current_")
        metadata = _json_loads(row.get("high_provenance_metadata"))
        claimed = claimed or (isinstance(metadata, dict) and "wrh_current_snapshot" in metadata)
        if not claimed:
            return False, None
        available = datetime.fromisoformat(str(row.get("fetched_at")).replace("Z", "+00:00"))
        if available.tzinfo is None:
            return True, None
        if available > as_of:
            # Only displaced states known to have been current are replayed.
            # Disputed incoming archive rows never acquire current authority.
            from src.state.schema.observation_prints_schema import receipt_us, receipt_us_sql
            # Applied current revisions have strictly increasing receipts. Seek
            # newest displaced eligible state and transfer only one bounded row,
            # rather than materializing every historical provenance document.
            previous = conn.execute(
                f"SELECT existing_row_json FROM {revisions} WHERE city=? AND target_date=? AND source=? "
                "AND writer='noaa_wrh_complete_current_product_v1' AND "
                + receipt_us_sql("json_extract(existing_row_json, '$.fetched_at')")
                + "<=? ORDER BY id DESC LIMIT 1",
                (city.name, target_date, source, receipt_us(as_of)),
            ).fetchone()
            if previous is None:
                return True, None
            row = json.loads(previous[0])
            metadata = _json_loads(row.get("high_provenance_metadata"))
        if not isinstance(metadata, dict):
            return True, None
        snapshot = replay_current_snapshot(metadata.get("wrh_current_snapshot"), city=city,
                                           target_date=target_date, as_of=as_of)
        confirmation = metadata.get("wrh_latest_confirmation")
        if isinstance(confirmation, dict) and confirmation.get("revision") == _WRH_ORDER_RECEIPT_REVISION:
            _read_wrh_order_receipt(confirmation, semantic_proof=snapshot.provenance(),
                                    content_identity=_current_wrh_content_identity(snapshot))
        elif confirmation is not None:
            confirmation_time = datetime.fromisoformat(str(confirmation.get("received_at")).replace("Z", "+00:00"))
            if confirmation_time.tzinfo is None:
                return True, None
            if confirmation_time <= as_of:
                confirmed = replay_current_snapshot(confirmation, city=city, target_date=target_date, as_of=as_of)
                if (confirmed.request_started_at < snapshot.request_started_at
                        or confirmed.received_at < snapshot.received_at
                        or _current_wrh_content_identity(confirmed) != _current_wrh_content_identity(snapshot)):
                    return True, None
        high, low = snapshot.extreme("high"), snapshot.extreme("low")
        expected_authority = "VERIFIED" if snapshot.complete_day and high is not None else "UNVERIFIED"
        if (row.get("authority") != expected_authority or row.get("station_id") != snapshot.station
                or row.get("unit") != snapshot.unit or row.get("source") != snapshot.source
                or row.get("high_temp") != (high.value if high else None)
                or row.get("low_temp") != (low.value if low else None)
                or row.get("fetched_at") != snapshot.received_at.isoformat()
                or row.get("high_fetch_utc") != row.get("fetched_at")
                or row.get("low_fetch_utc") != row.get("fetched_at")
                or _json_loads(row.get("low_provenance_metadata")) != metadata
                or metadata.get("payload_hash") != "sha256:" + snapshot.response_sha256):
            return True, None
        return True, snapshot
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError):
        return claimed, None
