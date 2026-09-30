# Created: 2026-09-30
# Last audited: 2026-09-30
# Authority basis: position_events growth (~0.75 GB/day): each MONITOR_REFRESHED row
#   carried a ~66 KB Day0 receipt whose `observation` block is ~98% three bulky
#   witnesses that repeat across rows. Provenance audit: writer
#   lifecycle_events.build_monitor_refreshed_canonical_write -> ledger.append_many_and_project;
#   verdict CURRENT_REUSABLE (no live reader consumes the bulky witnesses from position_events).
"""Content-addressed store for the bulky part of the Day0 monitor receipt.

``position_events.payload_json`` keeps the receipt with its bulky witnesses
(``HEAVY_KEYS`` under ``observation``) replaced by ``observation.heavy_sha256``.
The witnesses live once per distinct content in ``day0_receipt_blob`` as a zstd-3
canonical-JSON BLOB. Rows are immutable and content-addressed, so a reference can
never dangle and the table needs no eviction of its own: it is written in the same
transaction as, and only for, the ``position_events`` rows that reference it, and
those rows are never deleted (the trade_retention reachability ledger does not
touch ``position_events``).

Readers of the light fields (method, remaining_window, identities, observation
times) read the row directly. A reader that needs the full receipt calls
``resolve_receipt``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import zstandard

TABLE = "day0_receipt_blob"
RECEIPT_KEY = "day0_monitor_probability_receipt"
HEAVY_KEYS = (
    "statistical_probability_conditioning",
    "day0_causal_evidence_bundle",
    "day0_remaining_vector_witness",
)
REF_KEY = "heavy_sha256"
ENCODING = "zstd3+canonical-json-day0-receipt-heavy-v1"


def ensure_table(conn: sqlite3.Connection, table: str = TABLE) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            heavy_sha256 TEXT PRIMARY KEY,
            payload_encoding TEXT NOT NULL,
            payload BLOB NOT NULL,
            first_seen_at TEXT NOT NULL
        ) WITHOUT ROWID
        """
    )


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def split_receipt(receipt: dict) -> tuple[dict, str, bytes] | None:
    """Return (slim receipt, sha256, canonical heavy bytes), or None if nothing to split."""
    obs = receipt.get("observation")
    if not isinstance(obs, dict) or REF_KEY in obs:
        return None
    heavy = {k: obs[k] for k in HEAVY_KEYS if k in obs}
    if not heavy:
        return None
    raw = _canonical(heavy)
    sha = hashlib.sha256(raw).hexdigest()
    slim_obs = {k: v for k, v in obs.items() if k not in heavy}
    slim_obs[REF_KEY] = sha
    return {**receipt, "observation": slim_obs}, sha, raw


def externalize_event_payload(conn: sqlite3.Connection, event: dict) -> None:
    """Replace the receipt's bulky witnesses in ``event['payload_json']`` by a hash,
    storing them once. No-op for events without a splittable Day0 receipt."""
    text = event.get("payload_json")
    if not isinstance(text, str) or RECEIPT_KEY not in text:
        return
    payload = json.loads(text)
    receipt = payload.get(RECEIPT_KEY)
    split = split_receipt(receipt) if isinstance(receipt, dict) else None
    if split is None:
        return
    slim, sha, raw = split
    from src.state.owner_routed_write import owner_qualified_name

    table = owner_qualified_name(conn, TABLE)
    ensure_table(conn, table)
    conn.execute(
        f"INSERT OR IGNORE INTO {table} (heavy_sha256, payload_encoding, payload, first_seen_at) "
        "VALUES (?, ?, ?, ?)",
        (
            sha,
            ENCODING,
            zstandard.ZstdCompressor(level=3).compress(raw),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    payload[RECEIPT_KEY] = slim
    event["payload_json"] = json.dumps(payload, default=str, sort_keys=True)


def resolve_receipt(conn: sqlite3.Connection, receipt: dict) -> dict:
    """Full receipt: the row's receipt with its bulky witnesses restored by hash.

    A receipt that was never split (legacy row) is returned unchanged. A hash that
    does not resolve raises: a dangling reference is corruption, not absence.
    """
    obs = receipt.get("observation") if isinstance(receipt, dict) else None
    if not isinstance(obs, dict) or REF_KEY not in obs:
        return receipt
    sha = obs[REF_KEY]
    row = conn.execute(
        f"SELECT payload_encoding, payload FROM {TABLE} WHERE heavy_sha256 = ?", (sha,)
    ).fetchone()
    if row is None:
        raise LookupError(f"day0 receipt witnesses {sha} not found in {TABLE}")
    if row[0] != ENCODING:
        raise ValueError("day0 receipt witnesses encoding does not match the canonical format")
    raw = zstandard.ZstdDecompressor().decompress(row[1])
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError("day0 receipt witnesses sha256 does not match the reference")
    heavy = json.loads(raw)
    full_obs = {k: v for k, v in obs.items() if k != REF_KEY}
    full_obs.update(heavy)
    return {**receipt, "observation": full_obs}
