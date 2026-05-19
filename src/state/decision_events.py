# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4
# SCAFFOLD: decision_events writer + reader — production bodies pending T1 production pass

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts.kelly import EffectiveKellyContext
    from src.contracts.decision_source_context import DecisionSourceContext


@dataclass
class DecisionEventRow:
    """SCAFFOLD: mirrors decision_events table columns per scaffolds/t1_scaffold.md §3.

    29 columns total: 3 PK/time + 8 identity + 4 probability outputs
    + 5 PR-3 source context + 8 PR-6 source context + 2 provenance.
    Production pass adds field declarations and from_row() factory.
    """
    # SCAFFOLD: field declarations pending production pass


def write_decision_event(
    ctx: "DecisionSourceContext",
    ekc: "EffectiveKellyContext",
    intent: "ExecutionIntent",
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """SCAFFOLD: write a single decision_events row for a live decision.

    Production semantics:
    - conn=None → open get_world_connection(write_class="live")
    - decision_seq: SELECT COALESCE(MAX(decision_seq),-1)+1 WHERE decision_group_id=?
    - schema_version=SCHEMA_VERSION (13), source='live_decision'
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production")


def read_decision_event_by_group(
    decision_group_id: str,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[DecisionEventRow]:
    """SCAFFOLD: read all decision_events rows for a decision_group_id.

    Production semantics:
    - conn=None → open get_world_connection(write_class=None) (read-only)
    - Returns list ordered by decision_seq ASC
    - Returns [] if not found (caller falls back to Phase 0 temp storage per §4.6)
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production")
