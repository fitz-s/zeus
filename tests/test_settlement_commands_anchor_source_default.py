# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: operator codereview-may19 P1-3
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — timing-chain migration default for
#          polymarket_end_anchor_source must be 'unknown_legacy', not
#          'gamma_explicit'. Live-created rows that pass an empty value
#          also fall back to 'unknown_legacy'.
"""Antibody tests: polymarket_end_anchor_source default.

Pre-2026-05-19 P1-3 fix: the ADD COLUMN ALTER set
DEFAULT 'gamma_explicit', which fabricated authority for every historical
settlement_commands row that lacked a captured anchor source. Downstream
causal-evidence consumers (DecisionSourceContext, timing-chain audits)
would treat those rows as if Gamma's explicit endDate was the anchor,
when in fact the anchor source was unknown.

Fix: default both the migration column and the request_redeem fallback
to 'unknown_legacy'. Live callers must thread the actual anchor source
through the keyword argument explicitly; rows that don't bother are
honestly tagged as legacy.

Antibody contracts (sed-flip verifiable):
  T1: Column DEFAULT clause stringifies as 'unknown_legacy'.
  T2: request_redeem() with default polymarket_end_anchor_source produces
      a row whose stored value is 'unknown_legacy'.
  T3: request_redeem() with an explicit non-empty value stores it verbatim.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.execution.settlement_commands import (
    init_settlement_command_schema,
    request_redeem,
)
from src.state.db import init_schema


NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    init_settlement_command_schema(db)
    yield db
    db.close()


def test_t1_migration_default_is_unknown_legacy(conn):
    """T1: PRAGMA table_info shows the DEFAULT clause is 'unknown_legacy',
    not 'gamma_explicit'. Sed-flip: restore the old default → RED."""
    rows = conn.execute("PRAGMA table_info(settlement_commands)").fetchall()
    by_name = {r["name"]: r for r in rows}
    col = by_name.get("polymarket_end_anchor_source")
    assert col is not None, (
        "T1 setup FAIL: polymarket_end_anchor_source column is missing from "
        "settlement_commands. Schema migration regression."
    )
    default = str(col["dflt_value"] or "").strip("'\"")
    assert default == "unknown_legacy", (
        f"T1 antibody FAIL: polymarket_end_anchor_source DEFAULT = "
        f"{default!r}; expected 'unknown_legacy'. Fabricating 'gamma_explicit' "
        f"on legacy rows misleads downstream causal-evidence consumers."
    )


def test_t2_request_redeem_default_arg_stores_unknown_legacy(conn):
    """T2: request_redeem() without an explicit anchor source stores
    'unknown_legacy', not 'gamma_explicit'."""
    cmd_id = request_redeem(
        "0xcond_t2",
        "USDC",
        market_id="0xcond_t2",
        token_amounts={"yes": "1.0"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
    )
    row = conn.execute(
        "SELECT polymarket_end_anchor_source FROM settlement_commands WHERE command_id = ?",
        (cmd_id,),
    ).fetchone()
    assert row is not None
    assert row["polymarket_end_anchor_source"] == "unknown_legacy", (
        f"T2 antibody FAIL: stored anchor source = "
        f"{row['polymarket_end_anchor_source']!r}; expected 'unknown_legacy'. "
        f"Empty-string default must NOT fabricate 'gamma_explicit'."
    )


def test_t3_request_redeem_explicit_value_stored_verbatim(conn):
    """T3: an explicit non-empty value passed by the caller is stored
    unchanged. The legacy fallback only applies when caller passes empty."""
    cmd_id = request_redeem(
        "0xcond_t3",
        "USDC",
        market_id="0xcond_t3",
        token_amounts={"yes": "1.0"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
        polymarket_end_anchor_source="gamma_explicit",
    )
    row = conn.execute(
        "SELECT polymarket_end_anchor_source FROM settlement_commands WHERE command_id = ?",
        (cmd_id,),
    ).fetchone()
    assert row is not None
    assert row["polymarket_end_anchor_source"] == "gamma_explicit", (
        f"T3 antibody FAIL: stored anchor source = "
        f"{row['polymarket_end_anchor_source']!r}; expected 'gamma_explicit' "
        f"(explicit caller value). The fallback is corrupting verified rows."
    )
