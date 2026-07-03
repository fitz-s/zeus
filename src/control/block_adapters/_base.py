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
from types import ModuleType
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from src.control.entries_block_registry import Block, BlockCategory, BlockStage


@dataclass(frozen=True)
class RegistryDeps:
    """Runtime dependencies injected into entry-block registry adapters.

    Constructed by ``EntriesBlockRegistry.from_runtime(deps)`` using values
    available at the cycle_runner entry-control call site.
    """

    db_connection_factory: Callable[[], sqlite3.Connection]
    """Lazy factory for a fresh sqlite3.Connection to zeus.db."""

    risk_state_db_connection_factory: Callable[[], sqlite3.Connection]
    """Lazy factory for a fresh sqlite3.Connection to risk_state.db."""

    heartbeat_module: ModuleType
    """src.control.heartbeat_supervisor module reference."""

    ws_gap_guard_module: ModuleType
    """src.control.ws_gap_guard module reference."""


class BlockAdapter(Protocol):
    """Each adapter probes one runtime entry-blocking surface and returns a Block.

    Adapter rules:
    - Pure read: adapters never write state.
    - Exceptions become Block(state=UNKNOWN) so the snapshot remains visible.
    - Cheap: each probe must be < 50ms.
    - Single source: no retired or informational-only probes.
    """

    id: int
    """Stable runtime blocker id."""

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
