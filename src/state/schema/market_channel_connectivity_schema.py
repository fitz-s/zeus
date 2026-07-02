# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "input->q latency SLA (A2, 'THE metric')" / W0 "self-arming instrumentation" —
#   W0.2 packet (measure only, no gate, no enforcement).
"""Trade DB DDL owner for market_channel_connectivity_events (W0.2 blind-window metric).

WHY THIS TABLE EXISTS
----------------------
MarketChannelOnlineService (src/events/triggers/market_channel_ingestor.py) tracks
WS connect/disconnect/reconnect state in two dataclass fields — ``connected`` and
``gap_start`` — that live only in process memory. A daemon restart (the ~20-minute
churn observed on the live price-channel ingest daemon) silently resets this state,
so there was previously NO durable record of when the book subscription was actually
live. That means "detection floor" claims (the reactor's 60-90s scan cadence) could
not be checked against the truth of how much of the time the book feed was even
connected. This table makes that a queryable, restart-surviving fact.

TABLE SHAPE
-----------
Append-only event log (one row per connect/disconnect/reconnect transition), the
same shape as execution_feasibility_evidence and book_hash_transitions: durable
truth is the transition log, not a precomputed interval. Intervals are DERIVED at
read time — see BLIND_WINDOW_QUERY below — because writing them at write time would
require carrying gap-start state across a helper that must survive a mid-write crash
undetected; the append-only log needs no such bookkeeping and is trivially idempotent
(event_id is a stable hash of channel+transition+occurred_at, so a retried call is a
no-op).

DASHBOARD QUERY — blind-window intervals
-----------------------------------------
BLIND_WINDOW_QUERY pairs each 'disconnected' row with the next 'connected' row on the
same channel (via LEAD() over occurred_at) and returns the gap in seconds. Run it
against zeus_trades.db:

    SELECT channel, blind_window_start, blind_window_end, blind_window_seconds
    FROM (<BLIND_WINDOW_QUERY>)
    ORDER BY blind_window_start DESC;

LIMITATION (documented, not built in W0.2): a hard process kill (SIGKILL, OOM) never
reaches on_disconnect(), so it leaves no 'disconnected' row — the blind window in
that case is invisible to this table alone. Cross-reference against
state/daemon-heartbeat-price-channel-ingest.json (alive_at gaps) for full coverage;
that join is a follow-up (heartbeat history is not currently persisted anywhere —
see src/ingest/price_channel_daemon.py:_write_price_channel_heartbeat), out of W0.2
scope (measure only, additive, no new persistence beyond this packet's two metrics).
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_channel_connectivity_events (
    event_id TEXT NOT NULL PRIMARY KEY,
    channel TEXT NOT NULL,
    transition TEXT NOT NULL CHECK (transition IN ('connected', 'disconnected')),
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
)
"""

CREATE_INDEX_CHANNEL_TIME_SQL = """
CREATE INDEX IF NOT EXISTS idx_market_channel_connectivity_events_channel_time
    ON market_channel_connectivity_events(channel, occurred_at)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create market_channel_connectivity_events + index if they do not exist.

    Idempotent (IF NOT EXISTS). Wired into db.py init_schema_trade_only (trade DB,
    the live path) and called directly by tests that build an in-memory conn.
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_CHANNEL_TIME_SQL)


# Derives blind-window intervals from consecutive disconnected -> connected pairs
# on the same channel. A 'disconnected' row with no following 'connected' row
# (next_transition IS NULL) is an open/ongoing blind window — excluded here by the
# equality filter so the query only reports CLOSED intervals; callers who want the
# open tail can drop the ``next_transition = 'connected'`` clause.
BLIND_WINDOW_QUERY = """
WITH ordered AS (
    SELECT
        channel,
        transition,
        occurred_at,
        LEAD(transition) OVER (PARTITION BY channel ORDER BY occurred_at) AS next_transition,
        LEAD(occurred_at) OVER (PARTITION BY channel ORDER BY occurred_at) AS next_occurred_at
    FROM market_channel_connectivity_events
)
SELECT
    channel,
    occurred_at AS blind_window_start,
    next_occurred_at AS blind_window_end,
    (julianday(next_occurred_at) - julianday(occurred_at)) * 86400.0 AS blind_window_seconds
FROM ordered
WHERE transition = 'disconnected' AND next_transition = 'connected'
ORDER BY occurred_at
"""
