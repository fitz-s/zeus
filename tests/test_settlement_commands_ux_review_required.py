# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: OPS_FORENSICS.md F29 + PLAN.md WAVE-3.D
"""Antibody test: ux_settlement_commands_active_condition_asset excludes REDEEM_REVIEW_REQUIRED.

Verifies:
  1. Pre-migration: a REVIEW_REQUIRED row blocks a new command for the same triple.
  2. Post-migration: REVIEW_REQUIRED row does NOT block a new command (no IntegrityError).
  3. OPERATOR_REQUIRED still blocks (non-terminal; correct behavior).
  4. Migration is idempotent.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
MIGRATION_PATH = (
    REPO_ROOT / "scripts" / "migrations"
    / "202605_settlement_commands_ux_review_required.py"
)

_TABLE_DDL = """
CREATE TABLE settlement_commands (
    command_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN (
        'REDEEM_INTENT_CREATED','REDEEM_SUBMITTED','REDEEM_TX_HASHED',
        'REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_RETRYING',
        'REDEEM_REVIEW_REQUIRED','REDEEM_OPERATOR_REQUIRED'
    )),
    condition_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    payout_asset TEXT NOT NULL,
    requested_at TEXT NOT NULL DEFAULT '2026-05-17T00:00:00+00:00',
    tx_hash TEXT,
    block_number INTEGER,
    confirmation_count INTEGER DEFAULT 0,
    submitted_at TEXT,
    terminal_at TEXT,
    error_payload TEXT,
    pusd_amount_micro INTEGER,
    token_amounts_json TEXT
)
"""

# Original index (pre-migration) — excludes only CONFIRMED and FAILED
_OLD_INDEX_DDL = """
CREATE UNIQUE INDEX ux_settlement_commands_active_condition_asset
ON settlement_commands (condition_id, market_id, payout_asset)
WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED')
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_f29", MIGRATION_PATH)
    mod = types.ModuleType("mig_f29")
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_TABLE_DDL)
    conn.execute(_OLD_INDEX_DDL)
    conn.commit()
    return conn


class TestSettlementCommandsUxReviewRequired:

    def test_pre_migration_review_required_blocks(self):
        """Baseline: REVIEW_REQUIRED row blocks re-issue before migration."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO settlement_commands (command_id, state, condition_id, market_id, payout_asset) "
            "VALUES ('cmd-1', 'REDEEM_REVIEW_REQUIRED', 'cond-A', 'mkt-A', 'USDC')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO settlement_commands (command_id, state, condition_id, market_id, payout_asset) "
                "VALUES ('cmd-2', 'REDEEM_INTENT_CREATED', 'cond-A', 'mkt-A', 'USDC')"
            )

    def test_post_migration_review_required_does_not_block(self):
        """After migration, REVIEW_REQUIRED row allows re-issue for same triple."""
        mod = _load_migration()
        conn = _make_db()
        conn.execute(
            "INSERT INTO settlement_commands (command_id, state, condition_id, market_id, payout_asset) "
            "VALUES ('cmd-1', 'REDEEM_REVIEW_REQUIRED', 'cond-A', 'mkt-A', 'USDC')"
        )
        conn.commit()
        mod.up(conn)
        # Must not raise
        conn.execute(
            "INSERT INTO settlement_commands (command_id, state, condition_id, market_id, payout_asset) "
            "VALUES ('cmd-2', 'REDEEM_INTENT_CREATED', 'cond-A', 'mkt-A', 'USDC')"
        )

    def test_operator_required_still_blocks(self):
        """REDEEM_OPERATOR_REQUIRED is non-terminal and must still block."""
        mod = _load_migration()
        conn = _make_db()
        mod.up(conn)
        conn.execute(
            "INSERT INTO settlement_commands (command_id, state, condition_id, market_id, payout_asset) "
            "VALUES ('cmd-3', 'REDEEM_OPERATOR_REQUIRED', 'cond-B', 'mkt-B', 'USDC')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO settlement_commands (command_id, state, condition_id, market_id, payout_asset) "
                "VALUES ('cmd-4', 'REDEEM_INTENT_CREATED', 'cond-B', 'mkt-B', 'USDC')"
            )

    def test_migration_is_idempotent(self):
        mod = _load_migration()
        conn = _make_db()
        mod.up(conn)
        mod.up(conn)  # second call must not raise

    def test_confirmed_still_excluded(self):
        """REDEEM_CONFIRMED rows still excluded from the active constraint."""
        mod = _load_migration()
        conn = _make_db()
        mod.up(conn)
        conn.execute(
            "INSERT INTO settlement_commands (command_id, state, condition_id, market_id, payout_asset) "
            "VALUES ('cmd-5', 'REDEEM_CONFIRMED', 'cond-C', 'mkt-C', 'USDC')"
        )
        conn.commit()
        # Should succeed — CONFIRMED is excluded from the index
        conn.execute(
            "INSERT INTO settlement_commands (command_id, state, condition_id, market_id, payout_asset) "
            "VALUES ('cmd-6', 'REDEEM_INTENT_CREATED', 'cond-C', 'mkt-C', 'USDC')"
        )
