# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T4
#   ("durable coverage watermark ... one-time replay is not enough — a fill
#   landing after replay but before reader cutover is Attack A"). Mirrors the
#   T5 schema_epoch registration pattern (commits 3f857735b, c660592e1):
#   a single small marker table, wired into init_schema_trade_only, presence
#   only here — the synchronizer stamps/advances the row.
"""Trade DB DDL owner for fill_sync_watermarks — durable continuous-sync coverage.

WHY THIS TABLE EXISTS
----------------------
``src.ingest.fill_synchronizer`` polls the authenticated venue ``get_trades()``
surface on a schedule (not a one-time replay) so a fill that lands between a
migration replay and a reader cutover is still observed (LX-T4 Attack A). A
durable watermark per sync source is what lets that poller resume correctly
across process restarts instead of re-scanning from nothing (or worse, silently
starting from "now" and skipping the gap).

TABLE SHAPE
-----------
One row per sync source (``source`` is the primary key — there is exactly one
current coverage position per source, not a history log; the observation
history itself already lives in ``venue_trade_facts``). ``watermark_ts`` is the
venue timestamp coverage has advanced through; ``cursor`` is an optional
opaque pagination/sequence token for sources whose read surface exposes one
(the current polymarket_v2_adapter.get_trades() does not paginate by a server
cursor, so this is NULL there and freshness is judged by ``watermark_ts``
alone). ``coverage_note`` is a free-text operator/diagnostic annotation (e.g.
"backfilled from migration X"), never read by runtime logic.

Advance-after-persist discipline (packet law, enforced by the caller, not by
this schema): the watermark row is only UPDATEd after the batch of
``append_trade_fact`` calls for that cycle has committed — never before. See
``src.ingest.fill_synchronizer.sync_fills`` for the transaction shape.
"""
from __future__ import annotations

import sqlite3

TABLE_NAME = "fill_sync_watermarks"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fill_sync_watermarks (
    source          TEXT NOT NULL PRIMARY KEY,
    watermark_ts    TEXT,
    cursor          TEXT,
    updated_at      TEXT NOT NULL,
    coverage_note   TEXT NOT NULL DEFAULT ''
)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create fill_sync_watermarks if absent. Idempotent (IF NOT EXISTS).

    Wired into db.py init_schema_trade_only (trade DB, the live path) and
    called directly by tests that build an in-memory conn.
    """
    conn.execute(CREATE_TABLE_SQL)


__all__ = ["TABLE_NAME", "CREATE_TABLE_SQL", "ensure_table"]
