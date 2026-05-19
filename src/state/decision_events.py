# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.1–§4.2 (Path D natural-key reframe)
# SCAFFOLD: decision_events writer + reader — production bodies pending T1 production pass

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from src.contracts.decision_natural_key import DecisionNaturalKey

if TYPE_CHECKING:
    from src.contracts.execution_intent import DecisionSourceContext
    from src.contracts.effective_kelly_context import EffectiveKellyContext
    from src.contracts.execution_intent import ExecutionIntent


@dataclass
class DecisionEventRow:
    """SCAFFOLD: mirrors decision_events table columns per scaffolds/t1_scaffold.md.

    Natural-key PK (6 fields): market_id, condition_id, temperature_metric,
    target_date, observation_time, decision_seq.

    decision_group_id is audit-only (nullable on insert; populated by
    AFTER INSERT TRIGGER via Python UDF — see migration script §TRIGGER).

    18 NOT NULL columns (see scaffold §3.3).
    Production pass adds typed field declarations and from_row() factory.
    """
    # SCAFFOLD: field declarations pending production pass


def write_decision_event(
    natural_key: DecisionNaturalKey,
    ctx: "DecisionSourceContext",
    ekc: "EffectiveKellyContext",
    intent: "ExecutionIntent",
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """SCAFFOLD: write decision_events row for a live decision.

    conn=None → get_world_connection(write_class="live").
    decision_group_id NOT passed — populated by AFTER INSERT TRIGGER.
    decision_seq derived atomically under db_writer_lock(LIVE) + SAVEPOINT.
    Connection must have decision_group_id_v1 UDF bound (see migration script).
    schema_version=SCHEMA_VERSION (13), source='live_decision'.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production")


def read_decision_event_by_natural_key(
    key: DecisionNaturalKey,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[DecisionEventRow]:
    """SCAFFOLD: read rows by natural tuple. conn=None → get_world_connection_read_only().
    Returns [] if not found (caller falls back to Phase 0 temp storage per §4.5).
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production")


def read_decision_event_by_hash(
    decision_group_id: str,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[DecisionEventRow]:
    """SCAFFOLD: read rows by audit-only decision_group_id (idx_decision_events_hash).
    conn=None → get_world_connection_read_only(). Returns [] if not found.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production")
