# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Block adapters — one per gate, 13 total.

ALL_ADAPTERS is the canonical ordered list of adapter classes (gate id 1-13).
EntriesBlockRegistry.from_runtime() instantiates all of them.
"""

from __future__ import annotations

from src.control.block_adapters.fail_closed_tombstone import FailClosedTombstoneAdapter
from src.control.block_adapters.auto_pause_streak import AutoPauseStreakAdapter
from src.control.block_adapters.db_control_overrides import DbControlOverridesAdapter
from src.control.block_adapters.entries_paused_flag import EntriesPausedFlagAdapter
from src.control.block_adapters.entries_blocked_reason import EntriesBlockedReasonAdapter
from src.control.block_adapters.risk_level import RiskLevelAdapter
from src.control.block_adapters.trailing_loss_reference import TrailingLossReferenceAdapter
from src.control.block_adapters.bankroll_truth_source import BankrollTruthSourceAdapter
from src.control.block_adapters.heartbeat_health import HeartbeatHealthAdapter
from src.control.block_adapters.ws_gap_guard import WsGapGuardAdapter
from src.control.block_adapters.evaluator_rollout_gate import EvaluatorRolloutGateAdapter
from src.control.block_adapters.promotion_evidence_file import PromotionEvidenceFileAdapter
from src.control.block_adapters.rollout_gate_env_var import RolloutGateEnvVarAdapter

ALL_ADAPTERS: list[type] = [
    FailClosedTombstoneAdapter,    # gate 1
    AutoPauseStreakAdapter,        # gate 2
    DbControlOverridesAdapter,     # gate 3
    EntriesPausedFlagAdapter,      # gate 4
    EntriesBlockedReasonAdapter,   # gate 5
    RiskLevelAdapter,              # gate 6
    TrailingLossReferenceAdapter,  # gate 7
    BankrollTruthSourceAdapter,    # gate 8
    HeartbeatHealthAdapter,        # gate 9
    WsGapGuardAdapter,             # gate 10
    EvaluatorRolloutGateAdapter,   # gate 11
    PromotionEvidenceFileAdapter,  # gate 12
    RolloutGateEnvVarAdapter,      # gate 13
]

__all__ = [
    "ALL_ADAPTERS",
    "FailClosedTombstoneAdapter",
    "AutoPauseStreakAdapter",
    "DbControlOverridesAdapter",
    "EntriesPausedFlagAdapter",
    "EntriesBlockedReasonAdapter",
    "RiskLevelAdapter",
    "TrailingLossReferenceAdapter",
    "BankrollTruthSourceAdapter",
    "HeartbeatHealthAdapter",
    "WsGapGuardAdapter",
    "EvaluatorRolloutGateAdapter",
    "PromotionEvidenceFileAdapter",
    "RolloutGateEnvVarAdapter",
]
