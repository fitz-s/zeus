# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.1–§4.2 (Path D natural-key reframe, v3)
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
    """SCAFFOLD: mirrors decision_events table columns per scaffolds/t1_scaffold.md v3.

    Natural-key PK (5 fields): market_slug, temperature_metric, target_date,
    observation_time, decision_seq.
    condition_id is nullable enrichment — NOT in PK.

    decision_event_id is audit-only (deid_v1_ prefix; writer-side computed via
    decision_event_id_v1_hash; trigger backstop fires on NULL with sentinel
    'deid_v1_BACKSTOP_NULL_WRITER_BYPASS').

    18 NOT NULL columns (see scaffold §3.3 / ultraplan §4.2 v3).
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
    decision_event_id computed writer-side via decision_event_id_v1_hash() — Option β.
    decision_seq derived atomically under db_writer_lock(LIVE).
    schema_version=SCHEMA_VERSION (13), source='live_decision'.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production")


def read_decision_event_by_natural_key(
    key: DecisionNaturalKey,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[DecisionEventRow]:
    """SCAFFOLD: read rows by natural tuple (market_slug, temperature_metric,
    target_date, observation_time, decision_seq).
    conn=None → get_world_connection_read_only().
    Returns [] if not found (caller falls back to Phase 0 temp storage per §4.5).
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production")


def read_decision_event_by_event_id(
    decision_event_id: str,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[DecisionEventRow]:
    """SCAFFOLD: read rows by audit-only decision_event_id (idx_decision_events_event_id).
    Lookup is by deid_v1_ prefixed hash — NOT dgid_v1_ (calibration namespace).
    conn=None → get_world_connection_read_only(). Returns [] if not found.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production")
