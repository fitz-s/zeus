# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Runtime entry-block adapters.

ALL_ADAPTERS contains only probes whose BLOCKING or UNKNOWN state corresponds to
an actual live entry blocker. Retired and informational-only probes belong in
reports, not in the blocking registry.
"""

from __future__ import annotations

from src.control.block_adapters.db_control_overrides import DbControlOverridesAdapter
from src.control.block_adapters.risk_level import RiskLevelAdapter
from src.control.block_adapters.heartbeat_health import HeartbeatHealthAdapter
from src.control.block_adapters.ws_gap_guard import WsGapGuardAdapter

ALL_ADAPTERS: list[type] = [
    DbControlOverridesAdapter,
    RiskLevelAdapter,
    HeartbeatHealthAdapter,
    WsGapGuardAdapter,
]

__all__ = [
    "ALL_ADAPTERS",
    "DbControlOverridesAdapter",
    "RiskLevelAdapter",
    "HeartbeatHealthAdapter",
    "WsGapGuardAdapter",
]
