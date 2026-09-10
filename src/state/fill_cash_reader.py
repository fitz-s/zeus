"""Read-only, point-in-time validation of finalized fill cash evidence.

This reader deliberately does not write, open connections, or estimate fees.
It joins already-deduplicated local fill children to immutable chain proofs and
returns UNKNOWN whenever any identity, clock, proof, or conservation check is
ambiguous.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


_ALLOWED_SCHEMAS = {"main", "trades"}
_CHAIN_ID = 137
_ATOMS_PER_COLLATERAL = Decimal(1_000_000)


def _unknown(reason: str, *, proof_hashes: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "UNKNOWN", "reason": reason}
    if proof_hashes:
        result["proof_hashes"] = list(proof_hashes)
    return result


def _aware_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _captured_at_utc(value: object) -> datetime | None:
    parsed = _aware_utc(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str) and " " in value and "T" not in value:
        return _aware_utc(value.replace(" ", "T", 1) + "+00:00")
    return None


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _atoms(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not decimal.is_finite() or decimal <= 0:
        return None
    scaled = decimal * _ATOMS_PER_COLLATERAL
    if scaled != scaled.to_integral_value():
        return None
    return int(scaled)


def _json_obj(value: object) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _proof_hash(proof: Mapping[str, Any]) -> str | None:
    if "observed_at" in proof:
        return None
    try:
        encoded = json.dumps(
            dict(proof), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _decode_fill_cash_proof(**proof: Any) -> dict[str, Any] | None:
    """Call the pure decoder owned by the venue proof slice when available."""

    try:
        from src.venue.fill_cash_proof import decode_fill_cash_proof
    except (ImportError, ModuleNotFoundError):
        return None
    try:
        decoded = decode_fill_cash_proof(**proof)
    except Exception:  # noqa: BLE001 - proof failures are fail-closed UNKNOWN
        return None
    return decoded if isinstance(decoded, dict) else None


def _canonical_json(value: object) -> str | None:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None


def _event_atoms(event: Mapping[str, Any], field: str) -> int | None:
    value = event.get(field)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not decimal.is_finite() or decimal != decimal.to_integral_value() or decimal < 0:
        return None
    return int(decimal)


def _row_dict(cursor: sqlite3.Cursor, row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    names = [column[0] for column in cursor.description or ()]
    return dict(zip(names, row))


def _table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    try:
        return bool(conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone())
    except sqlite3.Error:
        return False


def _envelope_identity(
    conn: sqlite3.Connection, command: Mapping[str, Any], cutoff: datetime, schema: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    required = ("command_id", "venue_order_id", "token_id", "envelope_id")
    if any(_text(command.get(field)) is None for field in required):
        return None, None, "COMMAND_IDENTITY_MISSING"
    side = command.get("order_side") or command.get("side")
    if side not in {"BUY", "SELL"}:
        return None, None, "COMMAND_SIDE_INVALID"
    if not _table_exists(conn, schema, "venue_submission_envelopes"):
        return None, None, "ENVELOPE_TABLE_MISSING"
    if not _table_exists(conn, schema, "venue_commands"):
        return None, None, "COMMAND_TABLE_MISSING"
    try:
        pre_cursor = conn.execute(
            f"SELECT * FROM {schema}.venue_submission_envelopes WHERE envelope_id=?",
            (command["envelope_id"],),
        )
        pre_rows = [_row_dict(pre_cursor, row) for row in pre_cursor.fetchall()]
        command_cursor = conn.execute(
            f"SELECT * FROM {schema}.venue_commands WHERE venue_order_id=?",
            (command["venue_order_id"],),
        )
        command_rows = [_row_dict(command_cursor, row) for row in command_cursor.fetchall()]
    except sqlite3.Error:
        return None, None, "ENVELOPE_READ_FAILED"
    if len(pre_rows) != 1:
        return None, None, "PRE_SIGN_ENVELOPE_UNBOUND"
    if len(command_rows) != 1 or command_rows[0].get("command_id") != command["command_id"]:
        return None, None, "COMMAND_ORDER_ID_COLLISION"
    pre = pre_rows[0]
    pre_captured_at = _captured_at_utc(pre.get("captured_at"))
    if pre_captured_at is None or pre_captured_at >= cutoff:
        return None, None, "PRE_SIGN_ENVELOPE_PIT_UNBOUND"
    if (_text(pre.get("canonical_pre_sign_payload_hash")) is None
            or _text(pre.get("raw_request_hash")) is None):
        return None, None, "PRE_SIGN_HASHES_UNBOUND"
    if (pre.get("chain_id") != _CHAIN_ID
            or pre.get("funder_address") is None
            or pre.get("selected_outcome_token_id") != command["token_id"]
            or pre.get("side") != side):
        return None, None, "PRE_SIGN_IDENTITY_MISMATCH"
    try:
        signed_cursor = conn.execute(
            f"SELECT * FROM {schema}.venue_submission_envelopes WHERE order_id=?",
            (command["venue_order_id"],),
        )
        signed_rows = [_row_dict(signed_cursor, row) for row in signed_cursor.fetchall()]
    except sqlite3.Error:
        return None, None, "ENVELOPE_READ_FAILED"
    valid_signed: list[dict[str, Any]] = []
    for row in signed_rows:
        captured_at = _captured_at_utc(row.get("captured_at"))
        if captured_at is None or captured_at >= cutoff:
            continue
        blob = row.get("signed_order_blob")
        signed_hash = _text(row.get("signed_order_hash"))
        if blob in (None, b"", "") and not signed_hash:
            continue
        if not blob or not signed_hash:
            return None, None, "SIGNED_ENVELOPE_INVALID"
        blob_bytes = bytes(blob) if isinstance(blob, (bytes, bytearray, memoryview)) else str(blob).encode()
        if hashlib.sha256(blob_bytes).hexdigest() != signed_hash.lower():
            return None, None, "SIGNED_ENVELOPE_HASH_MISMATCH"
        if (row.get("chain_id") != _CHAIN_ID
                or row.get("funder_address") != pre.get("funder_address")
                or row.get("selected_outcome_token_id") != pre.get("selected_outcome_token_id")
                or row.get("side") != pre.get("side")
                or row.get("canonical_pre_sign_payload_hash") != pre.get("canonical_pre_sign_payload_hash")
                or row.get("raw_request_hash") != pre.get("raw_request_hash")
                or row.get("order_id") != command["venue_order_id"]):
            return None, None, "SIGNED_ENVELOPE_IDENTITY_MISMATCH"
        valid_signed.append(row)
    if not valid_signed:
        return None, None, "SIGNED_ENVELOPE_MISSING"
    signed_hashes = {str(row["signed_order_hash"]).lower() for row in valid_signed}
    if len(signed_hashes) != 1:
        return None, None, "SIGNED_ENVELOPE_CONFLICT"
    return pre, valid_signed[0], None


def _proof_row_valid(
    row: Mapping[str, Any], *, tx_hash: str, wallet: str, cutoff: datetime,
) -> tuple[dict[str, Any] | None, str | None]:
    observed = _aware_utc(row.get("observed_at"))
    if observed is None or observed >= cutoff:
        return None, "CHAIN_CASH_PROOF_CLOCK_UNBOUND"
    proof = _json_obj(row.get("proof_json"))
    if proof is None:
        return None, "CHAIN_CASH_PROOF_JSON_INVALID"
    if _proof_hash(proof) != str(row.get("proof_hash") or "").lower():
        return None, "CHAIN_CASH_PROOF_HASH_MISMATCH"
    if (proof.get("chain_id") != row.get("chain_id")
            or type(proof.get("rpc_chain_id")) is not int
            or proof.get("rpc_chain_id") != _CHAIN_ID
            or str(proof.get("tx_hash", "")).lower() != tx_hash
            or str(proof.get("wallet", "")).lower() != wallet):
        return None, "CHAIN_CASH_PROOF_IDENTITY_MISMATCH"
    decoded = proof.get("decoded")
    if not isinstance(decoded, dict):
        return None, "CHAIN_CASH_DECODED_MISSING"
    recomputed = _decode_fill_cash_proof(
        chain_id=proof.get("chain_id"), tx_hash=proof.get("tx_hash"), wallet=proof.get("wallet"),
        receipt=proof.get("receipt"), header=proof.get("header"),
        finalized_header=proof.get("finalized_header"),
        collateral_by_exchange=proof.get("collateral_by_exchange"),
        header_after=proof.get("header_after"),
    )
    if recomputed is None:
        return None, "CHAIN_CASH_DECODER_UNAVAILABLE_OR_FAILED"
    if _canonical_json(recomputed) != _canonical_json(decoded):
        return None, "CHAIN_CASH_DECODED_MISMATCH"
    if decoded.get("status") != row.get("status"):
        return None, "CHAIN_CASH_STATUS_MISMATCH"
    if str(decoded.get("reason") or "") != str(row.get("reason") or ""):
        return None, "CHAIN_CASH_REASON_MISMATCH"
    if decoded.get("status") != "PROVEN":
        return None, str(decoded.get("reason") or row.get("reason") or "CHAIN_CASH_UNKNOWN")
    if decoded.get("reason") in (None, ""):
        return None, "CHAIN_CASH_REASON_MISSING"
    return {"row": dict(row), "proof": proof, "decoded": decoded, "observed": observed}, None


def _event_economics(
    decoded: Mapping[str, Any], *, command: Mapping[str, Any], wallet: str,
    order_id: str,
) -> tuple[dict[str, int] | None, str | None]:
    events = decoded.get("events")
    if not isinstance(events, list) or not events:
        return None, "CHAIN_CASH_EVENTS_MISSING"
    seen: set[object] = set()
    all_shares = all_principal = all_fee = 0
    expected_wallet_delta = 0
    shares = principal = fee = 0
    target_found = False
    target_side = str((command.get("order_side") or command.get("side"))).upper()
    target_token = str(command["token_id"])
    for event in events:
        if not isinstance(event, Mapping):
            return None, "CHAIN_CASH_EVENT_INVALID"
        event_id = event.get("log_index", event.get("logIndex", event.get("event_id")))
        if event_id is None or event_id in seen:
            return None, "CHAIN_CASH_EVENT_DUPLICATE"
        seen.add(event_id)
        event_order = str(event.get("order_hash", ""))
        event_side = str(event.get("side", "")).upper()
        if event_side not in {"BUY", "SELL"}:
            return None, "CHAIN_CASH_EVENT_IDENTITY_MISMATCH"
        event_shares = _event_atoms(event, "shares_atoms")
        event_principal = _event_atoms(event, "principal_atoms")
        event_fee = _event_atoms(event, "fee_atoms")
        if event_shares is None or event_principal is None or event_fee is None:
            return None, "CHAIN_CASH_EVENT_ATOMS_INVALID"
        all_shares += event_shares
        all_principal += event_principal
        all_fee += event_fee
        event_delta = (-(event_principal + event_fee)
                       if event_side == "BUY" else event_principal - event_fee)
        expected_wallet_delta += event_delta
        if event_order == order_id:
            target_found = True
            if (str(event.get("token_id", "")) != target_token
                    or event_side != target_side
                    or str(event.get("maker", "")).lower() != wallet):
                return None, "CHAIN_CASH_EVENT_IDENTITY_MISMATCH"
            shares += event_shares
            principal += event_principal
            fee += event_fee
    decoded_delta = decoded.get("collateral_delta_atoms")
    if isinstance(decoded_delta, bool) or not isinstance(decoded_delta, int):
        return None, "CHAIN_CASH_COLLATERAL_DELTA_INVALID"
    side = str((command.get("order_side") or command.get("side"))).upper()
    if decoded_delta != expected_wallet_delta:
        return None, "CHAIN_CASH_COLLATERAL_CONSERVATION_MISMATCH"
    if not target_found:
        return None, "CHAIN_CASH_TARGET_EVENT_MISSING"
    for field, value in (
        ("shares_atoms", all_shares), ("principal_atoms", all_principal),
        ("fee_atoms", all_fee),
    ):
        if field in decoded and decoded.get(field) != value:
            return None, "CHAIN_CASH_EVENT_CONSERVATION_MISMATCH"
    target_delta = -(principal + fee) if side == "BUY" else principal - fee
    return {"shares_atoms": shares, "principal_atoms": principal,
            "fee_atoms": fee, "collateral_delta_atoms": target_delta}, None


def read_command_fill_cash(
    conn: sqlite3.Connection,
    *,
    command: dict,
    fills: list[dict],
    cutoff: datetime,
    schema: str = "main",
) -> dict[str, Any]:
    """Read one command's finalized cash proof at an immutable PIT cutoff."""

    if schema not in _ALLOWED_SCHEMAS:
        raise ValueError("unsupported cash fact schema")
    if not isinstance(command, Mapping) or not isinstance(fills, list):
        return _unknown("CASH_INPUT_INVALID")
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        return _unknown("CASH_CUTOFF_INVALID")
    cutoff = cutoff.astimezone(timezone.utc)
    if not _table_exists(conn, schema, "venue_fill_cash_facts"):
        return _unknown("CHAIN_CASH_TABLE_MISSING")
    pre, signed, reason = _envelope_identity(conn, command, cutoff, schema)
    if reason:
        return _unknown(reason)
    wallet = str(pre["funder_address"]).lower()
    if not fills:
        return _unknown("LOCAL_FILL_CHILDREN_MISSING")
    tx_children: dict[str, int] = defaultdict(int)
    for fill in fills:
        if not isinstance(fill, Mapping) or fill.get("venue_order_id") != command.get("venue_order_id"):
            return _unknown("LOCAL_FILL_IDENTITY_MISMATCH")
        tx_hash = _text(fill.get("tx_hash"))
        if tx_hash is None:
            return _unknown("LOCAL_FILL_TX_MISSING")
        share_atoms = _atoms(fill.get("filled_size"))
        if share_atoms is None:
            return _unknown("LOCAL_FILL_ATOMS_INVALID")
        tx_children[tx_hash.lower()] += share_atoms
    try:
        cursor = conn.execute(
            f"SELECT * FROM {schema}.venue_fill_cash_facts "
            "WHERE chain_id=? AND wallet=? AND tx_hash IN (%s) ORDER BY observed_at, id"
            % ",".join("?" for _ in tx_children),
            (_CHAIN_ID, wallet, *tx_children),
        )
        proof_rows = [_row_dict(cursor, row) for row in cursor.fetchall()]
    except sqlite3.Error:
        return _unknown("CHAIN_CASH_READ_FAILED")
    all_proof_hashes: list[str] = []
    by_tx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in proof_rows:
        tx_hash = str(row.get("tx_hash") or "").lower()
        if tx_hash not in tx_children:
            continue
        checked, row_reason = _proof_row_valid(row, tx_hash=tx_hash, wallet=wallet, cutoff=cutoff)
        if checked is None:
            continue
        all_proof_hashes.append(str(row.get("proof_hash")))
        by_tx[tx_hash].append(checked)
    if any(tx_hash not in by_tx for tx_hash in tx_children):
        return _unknown("CHAIN_CASH_PROOF_UNAVAILABLE", proof_hashes=all_proof_hashes)
    total = {"shares_atoms": 0, "principal_atoms": 0, "fee_atoms": 0, "collateral_delta_atoms": 0}
    collateral: str | None = None
    decimals: int | None = None
    available_at: datetime | None = None
    selected_hashes: list[str] = []
    for tx_hash, candidates in by_tx.items():
        identities: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for candidate in candidates:
            proof, decoded = candidate["proof"], candidate["decoded"]
            identity = _canonical_json({
                "header": proof.get("header"), "receipt": proof.get("receipt"),
                "collateral_by_exchange": proof.get("collateral_by_exchange"),
                "decoded": decoded,
            })
            if identity is not None:
                identities[identity].append(candidate)
        if len(identities) != 1:
            return _unknown("CHAIN_CASH_CONFLICT", proof_hashes=all_proof_hashes)
        candidates = next(iter(identities.values()))
        chosen = min(candidates, key=lambda item: (item["observed"], item["row"]["id"]))
        first_observed = chosen["observed"]
        economics, econ_reason = _event_economics(
            chosen["decoded"], command=command, wallet=wallet, order_id=str(command["venue_order_id"])
        )
        if economics is None or economics["shares_atoms"] != tx_children[tx_hash]:
            return _unknown(econ_reason or "CHAIN_CASH_SHARE_CONSERVATION_MISMATCH", proof_hashes=all_proof_hashes)
        decoded = chosen["decoded"]
        if type(decoded.get("decimals")) is not int or decoded["decimals"] != 6:
            return _unknown("CHAIN_CASH_DECIMALS_UNSUPPORTED", proof_hashes=all_proof_hashes)
        tx_collateral = _text(decoded.get("collateral"))
        if tx_collateral is None:
            return _unknown("CHAIN_CASH_COLLATERAL_MISSING", proof_hashes=all_proof_hashes)
        if collateral is None:
            collateral, decimals = tx_collateral, decoded["decimals"]
        elif collateral != tx_collateral or decimals != decoded["decimals"]:
            return _unknown("CHAIN_CASH_COLLATERAL_CONFLICT", proof_hashes=all_proof_hashes)
        for key in total:
            total[key] += economics[key]
        selected_hashes.append(str(chosen["row"]["proof_hash"]))
        available_at = max(available_at, first_observed) if available_at else first_observed
    result = {
        "status": "PROVEN", "reason": "FINALIZED_FILL_CASH_PROVEN",
        **total, "collateral": collateral, "decimals": decimals,
        "proof_hashes": selected_hashes,
        "available_at": available_at.isoformat() if available_at else None,
    }
    return result


__all__ = ["read_command_fill_cash"]
