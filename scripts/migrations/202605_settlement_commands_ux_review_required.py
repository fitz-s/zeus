# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Purpose: Extend ux_settlement_commands_active_condition_asset exclusion list
#   to include REDEEM_REVIEW_REQUIRED.
#
# Background (F29): REDEEM_REVIEW_REQUIRED is classified as terminal in
#   _TERMINAL_STATES (settlement_commands.py:100-104) but was absent from the
#   unique index exclusion, causing IntegrityError when an operator attempts to
#   re-issue a settlement command after a REVIEW_REQUIRED terminal.
#
# REDEEM_OPERATOR_REQUIRED is intentionally NOT added: it is non-terminal
#   (operator must resolve it before a new command can proceed), so blocking
#   duplicate commands for the same triple while OPERATOR_REQUIRED is active
#   is correct behavior.
#
# Index change is structural (SQLite partial-index WHERE clause is immutable
#   after CREATE). Pattern: DROP + recreate inside SAVEPOINT for live-safe swap.
#   SAVEPOINT (not BEGIN) allows retry-on-busy without outer-transaction collision.
#
# Authority: OPS_FORENSICS.md F29 + PLAN.md WAVE-3.D
# Depends on: fix/migration-runner-2026-05-17 (def up(conn) runner interface)
from __future__ import annotations

import sqlite3

_INDEX_NAME = "ux_settlement_commands_active_condition_asset"

# New WHERE clause — adds REDEEM_REVIEW_REQUIRED to the exclusion set
_NEW_WHERE = "WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_REVIEW_REQUIRED')"

_NEW_INDEX_DDL = (
    f"CREATE UNIQUE INDEX {_INDEX_NAME} "
    f"ON settlement_commands (condition_id, market_id, payout_asset) "
    f"{_NEW_WHERE}"
)


def _is_already_applied(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (_INDEX_NAME,),
    ).fetchone()
    if row is None:
        return False
    # Already applied if the new exclusion state is present
    return "REDEEM_REVIEW_REQUIRED" in (row[0] or "")


def _has_settlement_commands(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='settlement_commands'"
    ).fetchone()
    return row is not None


def up(conn: sqlite3.Connection) -> None:
    """Apply F29: add REDEEM_REVIEW_REQUIRED to unique index exclusion."""
    if not _has_settlement_commands(conn):
        print("202605_settlement_commands_ux_review_required: no settlement_commands table, skipping")
        return

    if _is_already_applied(conn):
        print("202605_settlement_commands_ux_review_required: already applied, skipping")
        return

    # Use SAVEPOINT for live-safe atomic index swap (allows retry-on-busy)
    conn.execute("SAVEPOINT ux_review_required_migration")
    try:
        conn.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
        conn.execute(_NEW_INDEX_DDL)
        conn.execute("RELEASE SAVEPOINT ux_review_required_migration")
    except Exception:
        try:
            conn.execute("ROLLBACK TO SAVEPOINT ux_review_required_migration")
            conn.execute("RELEASE SAVEPOINT ux_review_required_migration")
        except Exception:
            pass
        raise

    print(
        f"202605_settlement_commands_ux_review_required: applied — "
        f"{_INDEX_NAME} now excludes REDEEM_REVIEW_REQUIRED"
    )
