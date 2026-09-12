"""TRADE-owned public deliveries; never own fills, capital PnL or admission."""

from __future__ import annotations

import sqlite3


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS public_market_trade_observations (
            observation_id TEXT NOT NULL PRIMARY KEY,
            observer_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            end_sequence INTEGER NOT NULL CHECK (end_sequence >= sequence),
            connection_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('TRADE','STREAM_OPEN','STREAM_CLOSED','GAP')),
            received_at TEXT NOT NULL,
            source_observed_at TEXT,
            token_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            truth_status TEXT NOT NULL DEFAULT 'PUBLIC_DELIVERY_ONLY'
                CHECK (truth_status = 'PUBLIC_DELIVERY_ONLY'),
            coverage_status TEXT NOT NULL DEFAULT 'SOURCE_UNSEQUENCED'
                CHECK (coverage_status = 'SOURCE_UNSEQUENCED'),
            UNIQUE(observer_id, sequence)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_public_market_trade_token_time
        ON public_market_trade_observations(token_id, received_at)
    """)
    for operation in ("UPDATE", "DELETE"):
        conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS public_market_trade_no_{operation.lower()}
            BEFORE {operation} ON public_market_trade_observations
            BEGIN SELECT RAISE(ABORT, 'public trade observations are immutable'); END
        """)
