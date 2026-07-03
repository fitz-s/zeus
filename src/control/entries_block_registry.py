# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Entries Block Registry — runtime snapshot for current entry blockers.

Only probes whose BLOCKING or UNKNOWN state reflects an actual live entry
blocker belong here. Retired gates, duplicate derived fields, and
informational-only probes are intentionally excluded so the registry cannot
display fake blocking state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Mapping, Optional, Sequence

if TYPE_CHECKING:
    from src.control.block_adapters._base import BlockAdapter, RegistryDeps


# ── Enums ─────────────────────────────────────────────────────────────────────

class BlockCategory(str, Enum):
    DB_CONTROL_PLANE  = "db_control_plane"
    RISKGUARD         = "riskguard"
    RUNTIME_HEALTH    = "runtime_health"


class BlockStage(str, Enum):
    DISCOVERY = "discovery"


class BlockState(str, Enum):
    CLEAR    = "clear"
    BLOCKING = "blocking"
    UNKNOWN  = "unknown"   # adapter probe raised; unknown in the operator snapshot


# ── Priority order for first_blocker() ────────────────────────────────────────

_CATEGORY_PRIORITY: dict[BlockCategory, int] = {
    BlockCategory.DB_CONTROL_PLANE : 0,
    BlockCategory.RUNTIME_HEALTH   : 1,
    BlockCategory.RISKGUARD        : 2,
}


# ── Block dataclass ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Block:
    """Probe result for one gate.  Immutable — adapters return fresh instances."""

    id: int
    """Stable runtime blocker id."""

    name: str
    """Stable kebab/snake_case identifier."""

    category: BlockCategory
    stage: BlockStage
    state: BlockState

    blocking_reason: Optional[str]
    """Populated only when state == BLOCKING or UNKNOWN."""

    state_source: str
    """Human-readable source descriptor."""

    source_file_line: str
    """'src/control/control_plane.py:385' — citation that adapter probes."""

    owner_module: str
    """Owning module for the probed state surface."""

    owner_function: str
    """Owning function for the probed state surface."""

    raw_probe: Mapping[str, Any]
    """Debug payload — adapter-specific."""

    notes: str
    """Short caveat (1 line)."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict for cycle JSON embedding."""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category.value,
            "stage": self.stage.value,
            "state": self.state.value,
            "blocking_reason": self.blocking_reason,
            "state_source": self.state_source,
            "source_file_line": self.source_file_line,
            "owner_module": self.owner_module,
            "owner_function": self.owner_function,
            "raw_probe": dict(self.raw_probe),
            "notes": self.notes,
        }


# ── Registry ──────────────────────────────────────────────────────────────────

class EntriesBlockRegistry:
    """Operator snapshot for current runtime entry blockers.

    USAGE::

        registry = EntriesBlockRegistry.from_runtime(deps)
        blocks   = registry.enumerate_blocks(stage=BlockStage.DISCOVERY)
        blockers = registry.blocking_blocks(stage=BlockStage.DISCOVERY)
        first = registry.first_blocker(BlockStage.DISCOVERY)

    The registry is not a hidden entry gate. Runtime entry authority remains
    the explicit ``_discovery_gates_allow_entries`` argument list in
    ``src.engine.cycle_runner``. Adapters are probed lazily on the first call
    to any method, then cached for the lifetime of the registry instance. Each
    cycle creates a new registry instance, so there is no cross-cycle staleness.
    """

    def __init__(self, adapters: Sequence["BlockAdapter"]) -> None:
        self._adapters = list(adapters)
        self._cache: Optional[list[Block]] = None
        self._deps: Optional[RegistryDeps] = None

    @classmethod
    def from_runtime(cls, deps: "RegistryDeps") -> "EntriesBlockRegistry":
        """Build registry with current runtime-blocking adapters."""
        from src.control.block_adapters import ALL_ADAPTERS  # avoid circular at import time
        registry = cls([adapter_cls() for adapter_cls in ALL_ADAPTERS])
        registry._deps = deps
        return registry

    # ── Internal ─────────────────────────────────────────────────────────────

    def _probe_all(self) -> list[Block]:
        """Probe all adapters once and cache the results."""
        if self._cache is not None:
            return self._cache
        assert self._deps is not None, (
            "EntriesBlockRegistry.from_runtime() must be called before probing"
        )
        results: list[Block] = []
        for adapter in self._adapters:
            try:
                block = adapter.probe(self._deps)
            except Exception as exc:  # noqa: BLE001
                # Individual adapters already wrap their own exceptions, but
                # protect the snapshot itself so observability still emits.
                block = Block(
                    id=adapter.id,
                    name=adapter.name,
                    category=adapter.category,
                    stage=adapter.stage,
                    state=BlockState.UNKNOWN,
                    blocking_reason=(
                        f"registry_safety_net:{exc.__class__.__name__}: {exc}"
                    ),
                    state_source="unknown",
                    source_file_line=adapter.source_file_line,
                    owner_module="",
                    owner_function="",
                    raw_probe={"exception": str(exc)},
                    notes="registry-level safety net caught unexpected exception",
                )
            results.append(block)
        self._cache = sorted(results, key=lambda b: b.id)
        return self._cache

    # ── Public interface ──────────────────────────────────────────────────────

    def enumerate_blocks(
        self,
        stage: BlockStage | Literal["all"] = "all",
    ) -> list[Block]:
        """Return all blocks, optionally filtered by stage."""
        blocks = self._probe_all()
        if stage == "all":
            return list(blocks)
        return [b for b in blocks if b.stage == stage]

    def blocking_blocks(
        self,
        stage: BlockStage | Literal["all"] = "all",
    ) -> list[Block]:
        """Return blocks that are blocking or unknown in the operator snapshot."""
        return [
            b for b in self.enumerate_blocks(stage)
            if b.state in (BlockState.BLOCKING, BlockState.UNKNOWN)
        ]

    def is_clear(self, stage: BlockStage = BlockStage.DISCOVERY) -> bool:
        """Return True iff no snapshot probe is BLOCKING or UNKNOWN."""
        return len(self.blocking_blocks(stage)) == 0

    def first_blocker(self, stage: BlockStage) -> Optional[Block]:
        """Return highest-priority blocking snapshot probe.

        Priority order: DB_CONTROL_PLANE > RUNTIME_HEALTH > RISKGUARD.
        Within category, smaller ``id`` wins.
        """
        blockers = self.blocking_blocks(stage)
        if not blockers:
            return None
        return min(
            blockers,
            key=lambda b: (_CATEGORY_PRIORITY[b.category], b.id),
        )
