# Created: 2026-07-13
"""Synthetic fixture-DB helpers for src.reduce tests.

Same pattern as tests/reconcile/conftest.py and tests/state/test_fill_dedup.py:
real schema (src.state.db.init_schema + init_schema_trade_only), in-memory
sqlite, no network I/O. Insert helpers write the minimum columns each source
table's NOT NULL/CHECK constraints require, mirroring the exact patterns the
production writers use (append_trade_fact for venue_trade_facts,
build_position_identity_superseded_canonical_write's row shape for
position_events, src.ingest.payout_observer.append_observation for
payout_observations) so a fixture row is never structurally distinguishable
from a real one.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from src.state.db import init_schema, init_schema_trade_only
from src.state.venue_command_repo import append_trade_fact

NOW = "2026-07-13T12:00:00+00:00"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    init_schema_trade_only(c)
    yield c
    c.close()


def seed_fill_sync_watermark(
    conn: sqlite3.Connection,
    *,
    source: str = "polymarket_v2",
    watermark_ts: str = NOW,
    updated_at: str = NOW,
) -> None:
    conn.execute(
        """
        INSERT INTO fill_sync_watermarks (source, watermark_ts, cursor, updated_at, coverage_note)
        VALUES (?, ?, NULL, ?, '')
        """,
        (source, watermark_ts, updated_at),
    )
    conn.commit()


def insert_venue_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    position_id: str,
    intent_kind: str = "ENTRY",
    **overrides,
) -> None:
    defaults = dict(
        snapshot_id="snap-1",
        envelope_id="env-1",
        decision_id="dec-1",
        idempotency_key=f"idem-{command_id}",
        market_id="market-1",
        token_id="tok-yes",
        side="BUY" if intent_kind == "ENTRY" else "SELL",
        size=10.0,
        price=0.5,
        venue_order_id=f"vo-{command_id}",
        state="FILLED",
        created_at=NOW,
        updated_at=NOW,
    )
    defaults.update(overrides)
    columns = ["command_id", "position_id", "intent_kind", *defaults.keys()]
    values = [command_id, position_id, intent_kind, *defaults.values()]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO venue_commands ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()


_trade_fact_counter = {"n": 0}


def insert_trade_fact(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    filled_size: str,
    fill_price: str,
    trade_id: str | None = None,
    fee_paid_micro: int = 0,
    tx_hash: str | None = None,
    state: str = "CONFIRMED",
    source: str = "WS_USER",
    observed_at: str = NOW,
    venue_order_id: str | None = None,
) -> int:
    _trade_fact_counter["n"] += 1
    n = _trade_fact_counter["n"]
    trade_id = trade_id or f"trade-{n}"
    fact_id = append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=venue_order_id or f"vo-{command_id}",
        command_id=command_id,
        state=state,
        filled_size=filled_size,
        fill_price=fill_price,
        fee_paid_micro=fee_paid_micro,
        tx_hash=tx_hash,
        source=source,
        observed_at=observed_at,
        raw_payload_hash=hashlib.sha256(f"hash-{n}".encode()).hexdigest(),
    )
    conn.commit()
    return fact_id


def insert_identity_superseded(
    conn: sqlite3.Connection,
    *,
    keeper_position_id: str,
    absorbed_position_ids: list[str],
    sequence_no: int = 1,
    phase_after: str = "active",
    strategy_key: str = "edli",
    occurred_at: str = NOW,
) -> None:
    """Mirrors position_duplicate_consolidator._merge_equivalent_rows' write
    (F2) -- same builder, same 12-column INSERT shape."""
    from src.engine.lifecycle_events import build_position_identity_superseded_canonical_write

    event = build_position_identity_superseded_canonical_write(
        keeper_position_id=keeper_position_id,
        absorbed_position_ids=absorbed_position_ids,
        evidence_refs={"fixture": True},
        occurred_at=occurred_at,
        sequence_no=sequence_no,
        phase_after=phase_after,
        strategy_key=strategy_key,
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            event["position_id"],
            event["event_version"],
            event["sequence_no"],
            event["event_type"],
            event["occurred_at"],
            event["phase_before"],
            event["phase_after"],
            event["strategy_key"],
            event["source_module"],
            event["payload_json"],
            event["env"],
        ),
    )
    conn.commit()


def insert_payout_observation(
    conn: sqlite3.Connection,
    *,
    condition_id: str,
    outcome_index: int,
    state: str,
    payout_numerator: int | None = None,
    payout_denominator: int | None = None,
    block_number: int | None = 100,
    block_hash: str | None = "0xblock",
    observed_at: str = NOW,
) -> None:
    from src.ingest.payout_observer import append_observation

    append_observation(
        conn,
        condition_id=condition_id,
        outcome_index=outcome_index,
        payout_numerator=payout_numerator,
        payout_denominator=payout_denominator,
        state=state,
        block_number=block_number,
        block_hash=block_hash,
        observed_at=observed_at,
    )
    conn.commit()


def insert_position_current(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    condition_id: str | None = None,
    direction: str | None = "buy_yes",
    phase: str = "active",
    strategy_key: str = "edli",
    temperature_metric: str = "high",
    updated_at: str = NOW,
    **overrides,
) -> None:
    """Minimal position_current row -- only the 5 NOT NULL columns
    (position_id/phase/strategy_key/updated_at/temperature_metric) get
    defaults; every other column (including the fabricated economics
    columns this packet's reducer never reads) is NULL unless overridden.
    """
    defaults = dict(
        phase=phase,
        strategy_key=strategy_key,
        temperature_metric=temperature_metric,
        updated_at=updated_at,
        condition_id=condition_id,
        direction=direction,
    )
    defaults.update(overrides)
    columns = ["position_id", *defaults.keys()]
    values = [position_id, *defaults.values()]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO position_current ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
