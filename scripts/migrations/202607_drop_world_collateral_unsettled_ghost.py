# Lifecycle: created=2026-07-03; last_reviewed=2026-07-03; last_reused=2026-07-03
# Purpose: live restart recovery migration for an empty wrong-DB ghost table.
#   `collateral_unsettled_proceeds` is a trade-class table. A prior interrupted
#   restart/migration path created an empty copy on zeus-world.db, which makes
#   assert_db_matches_registry(WORLD) fail closed. Drop only the empty ghost; a
#   non-empty table is a hard stop because data would need audited relocation.
# Authority basis: runtime blocker observed 2026-07-03 during deploy_live
#   latest-main restart: src.main crashed before scheduler with
#   WORLD extra_on_disk=['collateral_unsettled_proceeds'] and row_count=0.
"""Drop an empty collateral_unsettled_proceeds ghost from zeus-world.db.

Runner interface: def up(conn: sqlite3.Connection) -> None
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "world"

_TABLE = "collateral_unsettled_proceeds"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def up(conn: sqlite3.Connection) -> None:
    if not _has_table(conn, _TABLE):
        conn.commit()
        return

    count = int(conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0])
    if count:
        raise RuntimeError(
            f"REFUSE_DROP_NONEMPTY_WORLD_GHOST:{_TABLE}:rows={count}"
        )

    conn.execute(f"DROP TABLE {_TABLE}")
    conn.commit()
