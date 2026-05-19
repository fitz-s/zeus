# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.1–§4.2 (Path D natural-key reframe, v3)

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from src.contracts.decision_natural_key import (
    DecisionNaturalKey,
    decision_event_id_v1_hash,
)

if TYPE_CHECKING:
    from src.contracts.execution_intent import DecisionSourceContext, ExecutionIntent
    from src.contracts.effective_kelly_context import EffectiveKellyContext


@dataclass
class DecisionEventRow:
    """Mirrors decision_events table columns per scaffolds/t1_scaffold.md v3.

    Natural-key PK (5 fields): market_slug, temperature_metric, target_date,
    observation_time, decision_seq.
    condition_id is nullable enrichment — NOT in PK.

    decision_event_id is audit-only (deid_v1_ prefix; writer-side computed via
    decision_event_id_v1_hash; trigger backstop fires on NULL with sentinel
    'deid_v1_BACKSTOP_NULL_WRITER_BYPASS').

    18 effective NOT NULL columns for live writes (source='live_decision');
    PR-6 timing fields nullable for backfill rows (source='phase0_backfill').
    """

    # Natural key (PK)
    market_slug: str
    temperature_metric: str
    target_date: str
    observation_time: str
    decision_seq: int

    # Nullable enrichment
    condition_id: Optional[str]

    # Audit-only hash
    decision_event_id: Optional[str]

    # Identity / time
    decision_time: str
    outcome: str
    side: str
    strategy_key: str
    cycle_id: Optional[str]
    cycle_iteration: Optional[int]

    # Probability outputs (live-only; NULL for backfill)
    p_posterior: Optional[float]
    edge: Optional[float]
    target_size_usd: Optional[float]
    target_price: Optional[float]

    # DecisionSourceContext — PR 3
    forecast_time: Optional[str]
    provider_reported_time: Optional[str]
    observation_available_at: str
    polymarket_end_anchor_source: str

    # DecisionSourceContext — PR 6 (timing chain; nullable for backfill)
    first_member_observed_time: Optional[str]
    run_complete_time: Optional[str]
    zeus_submit_intent_time: Optional[str]
    venue_ack_time: Optional[str]
    first_inclusion_block_time: Optional[str]
    finality_confirmed_time: Optional[str]
    clock_skew_estimate_ms_at_submit: Optional[int]
    raw_orderbook_hash_transition_delta_ms: Optional[int]

    # Provenance
    schema_version: int
    source: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DecisionEventRow":
        """Construct from a sqlite3.Row returned by a SELECT * on decision_events."""
        return cls(
            market_slug=row["market_slug"],
            temperature_metric=row["temperature_metric"],
            target_date=row["target_date"],
            observation_time=row["observation_time"],
            decision_seq=row["decision_seq"],
            condition_id=row["condition_id"],
            decision_event_id=row["decision_event_id"],
            decision_time=row["decision_time"],
            outcome=row["outcome"],
            side=row["side"],
            strategy_key=row["strategy_key"],
            cycle_id=row["cycle_id"],
            cycle_iteration=row["cycle_iteration"],
            p_posterior=row["p_posterior"],
            edge=row["edge"],
            target_size_usd=row["target_size_usd"],
            target_price=row["target_price"],
            forecast_time=row["forecast_time"],
            provider_reported_time=row["provider_reported_time"],
            observation_available_at=row["observation_available_at"],
            polymarket_end_anchor_source=row["polymarket_end_anchor_source"],
            first_member_observed_time=row["first_member_observed_time"],
            run_complete_time=row["run_complete_time"],
            zeus_submit_intent_time=row["zeus_submit_intent_time"],
            venue_ack_time=row["venue_ack_time"],
            first_inclusion_block_time=row["first_inclusion_block_time"],
            finality_confirmed_time=row["finality_confirmed_time"],
            clock_skew_estimate_ms_at_submit=row["clock_skew_estimate_ms_at_submit"],
            raw_orderbook_hash_transition_delta_ms=row["raw_orderbook_hash_transition_delta_ms"],
            schema_version=row["schema_version"],
            source=row["source"],
        )


def write_decision_event(
    natural_key: DecisionNaturalKey,
    ctx: "DecisionSourceContext",
    ekc: "EffectiveKellyContext",
    intent: "ExecutionIntent",
    *,
    strategy_key: str,
    p_posterior: Optional[float] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Write a decision_events row for a live decision.

    conn=None -> get_world_connection(write_class="live").
    decision_event_id computed writer-side via decision_event_id_v1_hash() — Option β.
    decision_seq derived atomically under db_writer_lock(LIVE).
    schema_version=SCHEMA_VERSION (13), source='live_decision'.

    strategy_key: caller-provided governance strategy identifier
        (one of 'settlement_capture', 'shoulder_sell', 'center_buy', 'opening_inertia').
    p_posterior: model posterior probability at decision time (optional; not yet in
        ExecutionIntent; caller passes when available from forecast pipeline).

    Enforces NOT NULL on PR-6 timing fields when source='live_decision':
    first_member_observed_time, run_complete_time, zeus_submit_intent_time,
    venue_ack_time are required for live rows (fail-fast before INSERT).
    """
    from src.state.db import SCHEMA_VERSION, ZEUS_WORLD_DB_PATH, get_world_connection
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    market_slug, temperature_metric, target_date, observation_time, _ = natural_key

    # Live-only enforcement of PR-6 timing fields
    _required_live = {
        "first_member_observed_time": ctx.first_member_observed_time,
        "run_complete_time": ctx.run_complete_time,
        "zeus_submit_intent_time": ctx.zeus_submit_intent_time,
        "venue_ack_time": ctx.venue_ack_time,
        "observation_available_at": ctx.observation_available_at,
        "polymarket_end_anchor_source": ctx.polymarket_end_anchor_source,
    }
    missing = [k for k, v in _required_live.items() if not v]
    if missing:
        raise ValueError(
            f"write_decision_event: live_decision requires non-empty fields: {missing}"
        )

    side = intent.direction.value if hasattr(intent.direction, "value") else str(intent.direction)

    own_conn = conn is None
    if own_conn:
        conn = get_world_connection(write_class=WriteClass.LIVE)

    try:
        with db_writer_lock(ZEUS_WORLD_DB_PATH, WriteClass.LIVE):
            # Derive decision_seq atomically under the LIVE lock
            row_seq = conn.execute(
                """
                SELECT COALESCE(MAX(decision_seq), -1) + 1
                FROM decision_events
                WHERE market_slug = ? AND temperature_metric = ?
                  AND target_date = ? AND observation_time = ?
                """,
                (market_slug, temperature_metric, target_date, observation_time),
            ).fetchone()[0]

            deid = decision_event_id_v1_hash(
                market_slug=market_slug,
                temperature_metric=temperature_metric,
                target_date=target_date,
                observation_time=observation_time,
                decision_seq=row_seq,
            )

            conn.execute(
                """
                INSERT INTO decision_events (
                    market_slug, temperature_metric, target_date,
                    observation_time, decision_seq,
                    condition_id, decision_event_id, decision_time,
                    outcome, side, strategy_key,
                    cycle_id, cycle_iteration,
                    p_posterior, edge, target_size_usd, target_price,
                    forecast_time, provider_reported_time,
                    observation_available_at, polymarket_end_anchor_source,
                    first_member_observed_time, run_complete_time,
                    zeus_submit_intent_time, venue_ack_time,
                    first_inclusion_block_time, finality_confirmed_time,
                    clock_skew_estimate_ms_at_submit,
                    raw_orderbook_hash_transition_delta_ms,
                    schema_version, source
                ) VALUES (
                    ?,?,?,?,?,
                    ?,?,?,
                    ?,?,?,
                    ?,?,
                    ?,?,?,?,
                    ?,?,
                    ?,?,
                    ?,?,
                    ?,?,
                    ?,?,
                    ?,
                    ?,
                    ?,?
                )
                """,
                (
                    market_slug, temperature_metric, target_date,
                    observation_time, row_seq,
                    None,  # condition_id: caller enriches if needed
                    deid, ctx.decision_time,
                    "pending", side, strategy_key,  # outcome=pending until settlement
                    None, None,  # cycle_id, cycle_iteration: future Phase-2
                    p_posterior,  # posterior probability from forecast pipeline (caller-supplied)
                    intent.decision_edge or None,  # edge: decision pipeline edge
                    float(intent.target_size_usd),
                    float(intent.limit_price),
                    ctx.forecast_available_at or None,
                    ctx.provider_reported_time or None,
                    ctx.observation_available_at,
                    ctx.polymarket_end_anchor_source,
                    ctx.first_member_observed_time or None,
                    ctx.run_complete_time or None,
                    ctx.zeus_submit_intent_time or None,
                    ctx.venue_ack_time or None,
                    ctx.first_inclusion_block_time or None,
                    ctx.finality_confirmed_time or None,
                    ctx.clock_skew_estimate_ms,
                    ctx.raw_orderbook_hash_transition_delta_ms,
                    SCHEMA_VERSION, "live_decision",
                ),
            )
            conn.commit()
    finally:
        if own_conn and conn is not None:
            conn.close()


def read_decision_event_by_natural_key(
    key: DecisionNaturalKey,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[DecisionEventRow]:
    """Read rows by natural tuple (market_slug, temperature_metric,
    target_date, observation_time); returns all decision_seq values ordered ASC.

    conn=None -> get_world_connection_read_only().
    Returns [] if not found (caller falls back to Phase 0 temp storage per §4.5).
    """
    from src.state.db import get_world_connection_read_only

    market_slug, temperature_metric, target_date, observation_time, _ = key

    own_conn = conn is None
    if own_conn:
        conn = get_world_connection_read_only()
        conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            """
            SELECT * FROM decision_events
            WHERE market_slug = ?
              AND temperature_metric = ?
              AND target_date = ?
              AND observation_time = ?
            ORDER BY decision_seq ASC
            """,
            (market_slug, temperature_metric, target_date, observation_time),
        ).fetchall()
        return [DecisionEventRow.from_row(r) for r in rows]
    finally:
        if own_conn and conn is not None:
            conn.close()


def read_decision_event_by_event_id(
    decision_event_id: str,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[DecisionEventRow]:
    """Read rows by audit-only decision_event_id (idx_decision_events_event_id).

    Lookup is by deid_v1_ prefixed hash — NOT dgid_v1_ (calibration namespace).
    Cross-namespace lookups raise ValueError to prevent silent sibling-table confusion.
    conn=None -> get_world_connection_read_only(). Returns [] if not found.
    """
    if decision_event_id.startswith("dgid_v1_"):
        raise ValueError(
            f"decision_event_id must use deid_v1_ namespace, not dgid_v1_. "
            f"Got {decision_event_id!r}. Cross-namespace lookups are forbidden."
        )

    from src.state.db import get_world_connection_read_only

    own_conn = conn is None
    if own_conn:
        conn = get_world_connection_read_only()
        conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            """
            SELECT * FROM decision_events
            WHERE decision_event_id = ?
            ORDER BY market_slug, target_date, decision_seq ASC
            """,
            (decision_event_id,),
        ).fetchall()
        return [DecisionEventRow.from_row(r) for r in rows]
    finally:
        if own_conn and conn is not None:
            conn.close()
