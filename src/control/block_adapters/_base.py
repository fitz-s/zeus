# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""BlockAdapter Protocol and RegistryDeps dataclass.

These are the shared types that bind the registry to the runtime without
importing from cycle_runner (which would create a circular dependency).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Callable, Mapping, Protocol

if TYPE_CHECKING:
    from src.control.entries_block_registry import Block, BlockCategory, BlockStage


@dataclass(frozen=True)
class RegistryDeps:
    """Runtime dependencies injected into all 13 adapters.

    Constructed by ``EntriesBlockRegistry.from_runtime(deps)`` using values
    available at the cycle_runner discovery short-circuit call site.  Each
    field corresponds to one or more GATE_AUDIT.yaml probe sources.
    """

    state_dir: Path
    """PROJECT_ROOT/state — used by file-based gates (1, 2, 12)."""

    db_connection_factory: Callable[[], sqlite3.Connection]
    """Lazy factory for a fresh sqlite3.Connection to zeus.db.
    Gates 3, 4, 5 use it to read control_overrides / control state.
    """

    risk_state_db_connection_factory: Callable[[], sqlite3.Connection]
    """Lazy factory for a fresh sqlite3.Connection to risk_state.db.
    Gates 6, 7, 8 use it.
    """

    riskguard_module: ModuleType
    """src.riskguard.riskguard module reference.
    Adapters call ``deps.riskguard_module._trailing_loss_reference(...)``
    and ``deps.riskguard_module.get_current_level()`` directly so that
    monkeypatching in tests works without patching the module string.
    """

    heartbeat_module: ModuleType
    """src.control.heartbeat_supervisor module reference.
    Gate 9: ``deps.heartbeat_module.summary()['entry']['allow_submit']``.
    """

    ws_gap_guard_module: ModuleType
    """src.control.ws_gap_guard module reference.
    Gate 10: ``deps.ws_gap_guard_module.summary()['entry']['allow_submit']``.
    """

    rollout_gate_module: ModuleType
    """src.control.entry_forecast_rollout module reference.
    Gate 11: ``deps.rollout_gate_module.evaluate_entry_forecast_rollout_gate(...)``.
    """

    env: Mapping[str, str]
    """os.environ snapshot — for gate 13 (ZEUS_ENTRY_FORECAST_ROLLOUT_GATE)."""


class BlockAdapter(Protocol):
    """Each adapter probes ONE gate and returns a Block.

    Adapter rules (from REGISTRY_DESIGN.md):
    - Pure read: adapters never write state.
    - Fail-closed: exception → Block(state=UNKNOWN).
    - Cheap: each probe must be < 50ms.
    - Single source: one adapter per gate id.
    """

    id: int
    """Matches GATE_AUDIT.yaml id (1-13)."""

    name: str
    """Stable kebab/snake_case identifier."""

    category: "BlockCategory"
    """BlockCategory enum value."""

    stage: "BlockStage"
    """BlockStage enum value."""

    source_file_line: str
    """Static citation: 'src/control/control_plane.py:385'."""

    def probe(self, deps: RegistryDeps) -> "Block":
        """Probe the gate and return a Block describing its state."""
        ...
