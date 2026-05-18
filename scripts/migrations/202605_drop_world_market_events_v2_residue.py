# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Authority basis: docs/operations/task_2026-05-16_deep_alignment_audit/FIX_PLAN.md §5 PR-A (F4)
#   POST_K1_DELTA.md F4 row: 2,112 stranded rows on world.market_events_v2
#   Writer retargeted to forecasts.db via PR #121; world copy is dead data.
#   Canonical reader uses forecasts.market_events_v2 (confirmed grep pre-flight).
# DB target: zeus-world.db (world.market_events_v2 ghost table, legacy_archived)
# Runner interface: def up(conn: sqlite3.Connection) -> None


def up(conn):
    """Drop the 2,112-row residual market_events_v2 table from zeus-world.db.

    Background: Before PR #121, the harvester truth writer wrote market_events_v2
    rows to the connection that happened to resolve to zeus-world.db (trades-rooted
    connection). PR #121 explicitly opened zeus-forecasts.db for all market_events_v2
    writes. The 2,112 rows on world.db are orphaned — no reader uses them
    (all readers target ZEUS_FORECASTS_DB_PATH explicitly per grep pre-flight).

    Idempotent: asserts presence (table must exist) only on first run;
    subsequent runs tolerate absence gracefully via the early-exit guard.

    Pre-conditions asserted:
    1. world.market_events_v2 row count must be within ±10% of 2,112 (allows drift).
    2. Connection parameter must be zeus-world.db (not forecasts.db) — verified
       by callers; this function does NOT cross-write to forecasts.db.

    Post-condition: market_events_v2 table is absent from world.db.
    """
    # Idempotency guard: table may already be gone (re-run safety)
    table_exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='market_events_v2'"
    ).fetchone()[0]
    if not table_exists:
        return  # Already dropped — idempotent re-run

    # Pre-condition 1: row count within ±10% of expected stale 2,112
    row_count = conn.execute("SELECT COUNT(*) FROM market_events_v2").fetchone()[0]
    expected = 2112
    tolerance = int(expected * 0.10)  # 211 rows
    if abs(row_count - expected) > tolerance:
        raise AssertionError(
            f"world.market_events_v2 row count {row_count} deviates >10% from "
            f"expected {expected} (tolerance ±{tolerance}). Inspect before dropping. "
            "If intentional, update the expected constant in this migration."
        )

    conn.execute("DROP TABLE market_events_v2")
