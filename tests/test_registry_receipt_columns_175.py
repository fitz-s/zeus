# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: docs/operations/CONSOLIDATED_AUDIT_AND_PLAN_2026-06-04.md
#                  R4/#175 — the receipt writer's _ensure_column-migrated columns
#                  must be in required_columns so a stale live DB FATALs at boot
#                  instead of silently failing 37 receipt writes as no-trades.
# Lifecycle: created=2026-06-04; last_reviewed=2026-06-05; last_reused=2026-06-05
# Purpose: Boot-guard antibody — _ensure_column-migrated receipt columns must be in required_columns so a stale live DB FATALs at boot, not silently fail receipt writes as no-trades (R4/#175).
# Reuse: Re-run when the edli_no_submit_receipts schema or assert_db_matches_registry column set changes.
"""Boot-guard antibody for the edli_no_submit_receipts column-drift defect.

Live evidence (state/zeus-world.db, 2026-06-03T14:21-14:25): 37x
`table edli_no_submit_receipts has no column named mainstream_agreement_pass`
write failures recorded as per-candidate no-trades. The columns were added to
the schema via _ensure_column AFTER table creation, so a live world DB whose
migration lagged the code deploy lacked them. The boot guard
(assert_db_matches_registry) only checked the 6 creation columns and booted
happily while writes silently failed.

This pins that the migrated columns are now in the registry's required_columns,
so the boot guard FATALs (loud) on a stale DB rather than letting it run.

All tests use :memory: SQLite. No production DB writes.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import init_schema
from src.state.table_registry import (
    DBIdentity,
    RegistryAssertionError,
    assert_db_matches_registry,
    required_columns_for,
)

# The _ensure_column-migrated columns that the writer INSERTs and a stale DB
# could lack. Must all be declared so the guard catches any one going missing.
_MIGRATED_COLS = [
    "kelly_decision_id",
    "risk_decision_id",
    "mainstream_agreement_pass",
    "mainstream_agreement_fail_reason",
    "mainstream_point",
    "mainstream_delta",
    "mainstream_bin_label",
    "mainstream_source",
    "mainstream_fetched_at_utc",
    "alpha_gap",
]


def test_required_columns_declares_migrated_receipt_columns():
    """Every writer-INSERTed migrated column is declared in required_columns."""
    declared = {c.name for c in (required_columns_for("edli_no_submit_receipts") or [])}
    missing = [c for c in _MIGRATED_COLS if c not in declared]
    assert not missing, f"required_columns missing migrated receipt columns: {missing}"


def test_fresh_world_db_passes_registry():
    """A fully-migrated (fresh init_schema) world DB must boot-check clean."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    assert_db_matches_registry(conn, DBIdentity.WORLD)  # must not raise


@pytest.mark.parametrize("dropped", ["mainstream_agreement_pass", "alpha_gap"])
def test_stale_world_db_missing_migrated_column_is_fatal(dropped):
    """A world DB missing a migrated receipt column must FATAL at boot.

    Reproduces the 2026-06-03 stale-DB state. Before #175 this column was not in
    required_columns, so the guard passed and 37 writes silently failed; now the
    guard raises RegistryAssertionError naming the missing column.
    """
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute(f"ALTER TABLE edli_no_submit_receipts DROP COLUMN {dropped}")
    with pytest.raises(RegistryAssertionError, match=dropped):
        assert_db_matches_registry(conn, DBIdentity.WORLD)
