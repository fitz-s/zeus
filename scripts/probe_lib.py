# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: session retrospective 2026-06-12 (operator: 一行级别现在就开始做) —
#   two diagnosis rounds were burned on (a) datetime('now') vs ISO-T lexicographic
#   mismatch silently matching all rows, (b) unguarded $(sqlite3 ...) watch loops
#   false-firing on transient DB locks.
"""Tiny shared helpers for read-only observation probes against the live Zeus DBs.

LAWS this module encodes (use it instead of re-deriving them per probe):

1. TIME CUTOFFS: Zeus timestamps are ISO-8601 with a 'T' separator
   ('2026-06-12T18:09:00+00:00').  SQLite's datetime('now') renders with a
   SPACE separator, and 'T' (0x54) > ' ' (0x20), so the comparison
   ``created_at > datetime('now','-2 hours')`` lexicographically admits EVERY
   same-day T-format row — the window silently becomes "all of today"
   (observed 2026-06-12: a 2h riskguard-block count of 2113 was actually the
   full day).  Always compare against an explicit ISO-T string from
   :func:`iso_cutoff`.

2. READ-ONLY: probes open live DBs only via mode=ro URIs (:func:`ro`).

3. WATCH LOOPS: a shell condition like ``[ "$(sqlite3 ...)" != "0" ]`` is a
   FALSE-POSITIVE machine — on a transient lock/IO error the command
   substitution yields an empty string, and '' != '0' fires the watch
   (observed 2026-06-12: a phantom "FIRST REDECISION EVENT").  Use
   :func:`guarded_watch_clause` to emit the guarded shell form, or poll in
   Python via :func:`count_when_readable`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

ZEUS = "/Users/leofitz/zeus/state"
WORLD = f"{ZEUS}/zeus-world.db"
TRADES = f"{ZEUS}/zeus_trades.db"
FORECASTS = f"{ZEUS}/zeus-forecasts.db"


def ro(db_path: str, timeout: float = 5.0) -> sqlite3.Connection:
    """Read-only connection to a live DB (the only sanctioned probe mode)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn


def iso_cutoff(hours: float = 0.0, minutes: float = 0.0) -> str:
    """UTC cutoff string in the SAME ISO-T format Zeus persists.

    ``WHERE created_at > ?`` with this value is the ONLY correct recency
    filter against T-format timestamp columns (see module docstring, law 1).
    """
    dt = datetime.now(timezone.utc) - timedelta(hours=hours, minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def count_when_readable(db_path: str, sql: str, params: tuple = ()) -> int | None:
    """Run a COUNT-style scalar query; None (never 0) on lock/IO error.

    Watch loops must treat None as 'try again', never as a fired condition.
    """
    try:
        conn = ro(db_path, timeout=2.0)
        try:
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row is not None else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def guarded_watch_clause(db_path: str, sql_no_quotes: str) -> str:
    """Emit the guarded shell until-loop condition for a sqlite watch.

    The emitted form only fires when the query SUCCEEDED and returned a
    non-empty, non-zero value — a lock/error keeps waiting instead of
    false-firing::

        until out=$(sqlite3 "file:DB?mode=ro" "SQL" 2>/dev/null) \
              && [ -n "$out" ] && [ "$out" != "0" ]; do sleep 60; done
    """
    return (
        f'until out=$(sqlite3 "file:{db_path}?mode=ro" "{sql_no_quotes}" 2>/dev/null) '
        f'&& [ -n "$out" ] && [ "$out" != "0" ]; do sleep 60; done'
    )
