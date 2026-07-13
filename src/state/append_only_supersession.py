# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Round-2 delta
#   adjudication ("mutable learning receipts"): live_profit_audit.py's
#   LiveProfitAuditLedger.insert_record and settlement_skill_attribution.py's
#   persist_grade both mutate an existing row via ON CONFLICT DO UPDATE — a rerun
#   can silently replace an earlier analytical result and destroy the precise
#   corpus that produced a historical model decision. Both are converted to
#   append-only + supersession using this ONE shared helper (the smaller schema
#   change: a whole-row JSON snapshot per superseded version, not a mirrored
#   column set duplicated per table).
"""Shared archive-before-overwrite helper for append-only learning receipts.

The pattern: before a caller performs its own ON CONFLICT DO UPDATE against a
"current" table (whose read contract stays single-row-per-natural-key so no
downstream reader needs updating), it calls ``archive_row_before_overwrite`` to
snapshot the CURRENT row's full pre-image into a permanent, append-only,
never-updated sibling ``<table>_supersessions`` table. A first-time INSERT (no
existing row) is a no-op — there is nothing to supersede yet.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any


def archive_row_before_overwrite(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_column: str,
    key_value: str,
    supersessions_table: str,
    new_id: str,
    now_iso: str,
) -> None:
    """Archive ``table``'s current row for ``key_column = key_value`` (if any).

    Writes one row into ``supersessions_table`` (schema:
    ``supersession_id, <key_column>, prior_row_json, superseded_by,
    superseded_at, schema_version`` — see edli_live_profit_audit_schema.py /
    settlement_attribution_schema.py) carrying the FULL prior row as a JSON
    snapshot. ``new_id`` is the identity (e.g. the new attribution_id/audit_id)
    the caller is about to write in place of the archived row, recorded as
    ``superseded_by`` so the supersession chain is traceable forward. No-op when
    no existing row is present (nothing to supersede).

    Table/column names are fixed, code-controlled constants passed by trusted
    callers — never user input — so f-string identifier interpolation here
    matches the existing codebase pattern (e.g. schema-qualified table names in
    src/execution/executor.py).
    """
    cur = conn.execute(
        f"SELECT * FROM {table} WHERE {key_column} = ?",  # noqa: S608
        (key_value,),
    )
    row = cur.fetchone()
    if row is None:
        return
    columns = [d[0] for d in cur.description]
    prior: dict[str, Any] = {col: row[i] for i, col in enumerate(columns)}
    conn.execute(
        f"""
        INSERT INTO {supersessions_table} (
            supersession_id, {key_column}, prior_row_json, superseded_by,
            superseded_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, 1)
        """,  # noqa: S608
        (
            uuid.uuid4().hex,
            key_value,
            json.dumps(prior, default=str, sort_keys=True),
            new_id,
            now_iso,
        ),
    )
