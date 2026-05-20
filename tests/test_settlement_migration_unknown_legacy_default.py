# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19.md P1-3 / codereview-may19-2.md P1-3
"""Antibody: legacy DB migration must NOT produce 'gamma_explicit' rows.

P1-3 (codereview-may19.md / codereview-may19-2.md): a previous migration used
DEFAULT 'gamma_explicit' for the polymarket_end_anchor_source column, which
fabricated verified-provenance for rows whose authority chain was never
captured.

Fix: the column DEFAULT is 'unknown_legacy'. Rows that already carry
'gamma_explicit' (written by the old migration) are corrected to 'unknown_legacy'
by the V13 backfill UPDATE in ensure_settlement_schema_ready.

Antibody contracts:
  C1: after ensure_settlement_schema_ready on a legacy DB that already has
      rows with polymarket_end_anchor_source = 'gamma_explicit' (pre-migration
      artifact), those rows MUST have been converted to 'unknown_legacy'.
  C2: a new row written after migration with no explicit anchor source gets
      'unknown_legacy' (column DEFAULT), not 'gamma_explicit'.
  C3: a new row written with an explicit non-empty anchor source (e.g.
      'gamma_explicit' set deliberately by a live caller) retains that value.

Sed-flip target: change V13 backfill UPDATE to update WHERE ... = 'gamma_explicit'
to a no-op (e.g. WHERE 1=0) → C1 goes RED (old fabricated rows survive).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.execution.settlement_commands import (
    SettlementState,
    SETTLEMENT_COMMAND_SCHEMA,
    ensure_settlement_schema_ready,
    request_redeem,
)

NOW = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)


def _build_legacy_db_with_gamma_explicit_rows() -> sqlite3.Connection:
    """Simulate a pre-migration DB: create base schema + add column with
    old DEFAULT 'gamma_explicit', then insert rows that carry that value."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SETTLEMENT_COMMAND_SCHEMA)
    # Simulate the OLD migration that set DEFAULT 'gamma_explicit'
    try:
        db.execute(
            "ALTER TABLE settlement_commands ADD COLUMN "
            "polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit'"
        )
    except Exception as exc:
        if "duplicate column" not in str(exc).lower():
            raise
    # Insert two rows that end up with 'gamma_explicit' from the old DEFAULT
    for i, cond in enumerate(["0x" + "aa" * 32, "0x" + "bb" * 32]):
        db.execute(
            """INSERT INTO settlement_commands
               (command_id, state, condition_id, market_id, payout_asset, requested_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                f"legacy-{i}",
                SettlementState.REDEEM_INTENT_CREATED.value,
                cond,
                cond,
                "USDC",
                NOW.isoformat(),
            ),
        )
    db.commit()
    return db


def test_c1_gamma_explicit_rows_converted_to_unknown_legacy():
    """C1: rows carrying 'gamma_explicit' from old migration are converted
    to 'unknown_legacy' after ensure_settlement_schema_ready runs."""
    db = _build_legacy_db_with_gamma_explicit_rows()

    # Verify pre-condition: rows currently have 'gamma_explicit'
    pre_rows = db.execute(
        "SELECT polymarket_end_anchor_source FROM settlement_commands"
    ).fetchall()
    assert all(r[0] == "gamma_explicit" for r in pre_rows), (
        "Test setup error: expected pre-migration rows with 'gamma_explicit'"
    )

    # Run migration
    ensure_settlement_schema_ready(db)

    post_rows = db.execute(
        "SELECT command_id, polymarket_end_anchor_source FROM settlement_commands"
    ).fetchall()
    bad_rows = [r for r in post_rows if r["polymarket_end_anchor_source"] == "gamma_explicit"]
    assert not bad_rows, (
        f"C1 FAIL: {len(bad_rows)} rows still carry 'gamma_explicit' after migration. "
        f"command_ids={[r['command_id'] for r in bad_rows]}. "
        "The V13 backfill UPDATE is not executing or is targeting wrong rows."
    )
    for r in post_rows:
        assert r["polymarket_end_anchor_source"] == "unknown_legacy", (
            f"C1 FAIL: row {r['command_id']!r} has "
            f"anchor_source={r['polymarket_end_anchor_source']!r}, expected 'unknown_legacy'"
        )
    db.close()


def test_c2_new_row_without_explicit_source_gets_unknown_legacy():
    """C2: new rows created without an explicit polymarket_end_anchor_source
    get 'unknown_legacy' (column DEFAULT), not 'gamma_explicit'."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_settlement_schema_ready(db)

    cmd_id = request_redeem(
        "0x" + "cc" * 32,
        "USDC",
        market_id="0x" + "cc" * 32,
        conn=db,
        # No polymarket_end_anchor_source passed → defaults to 'unknown_legacy'
    )
    db.commit()

    row = db.execute(
        "SELECT polymarket_end_anchor_source FROM settlement_commands WHERE command_id = ?",
        (cmd_id,),
    ).fetchone()
    assert row["polymarket_end_anchor_source"] == "unknown_legacy", (
        f"C2 FAIL: new row without explicit source has "
        f"anchor_source={row['polymarket_end_anchor_source']!r}; expected 'unknown_legacy'"
    )
    db.close()


def test_c3_explicit_anchor_source_retained():
    """C3: a row written with an explicit 'gamma_explicit' anchor source
    (set by a live caller that has actual Gamma evidence) must retain that value."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_settlement_schema_ready(db)

    cmd_id = request_redeem(
        "0x" + "dd" * 32,
        "USDC",
        market_id="0x" + "dd" * 32,
        conn=db,
        polymarket_end_anchor_source="gamma_explicit",
    )
    db.commit()

    row = db.execute(
        "SELECT polymarket_end_anchor_source FROM settlement_commands WHERE command_id = ?",
        (cmd_id,),
    ).fetchone()
    assert row["polymarket_end_anchor_source"] == "gamma_explicit", (
        f"C3 FAIL: explicit 'gamma_explicit' was overwritten to "
        f"{row['polymarket_end_anchor_source']!r}. Live callers must be able to "
        "set a verified anchor source."
    )
    db.close()
