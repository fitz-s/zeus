# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.3)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Pre-Action Validator"
"""
rules/task_registry — TaskRegistry class.

Wraps a list[TaskCatalogEntry] with look-up, filtering, and cross-validation
methods. Constructed from a loaded catalog; does not perform I/O itself.

Cross-check contract (SCAFFOLD §4):
  Any task that declares dry_run_floor_exempt=True in the YAML catalog
  MUST appear in FLOOR_EXEMPT_TASK_IDS (imported from
  maintenance_worker.core.install_metadata). If a task claims exemption
  but is not in the hardcoded frozenset, TaskRegistry.__init__ raises
  UnauthorizedExemptionError at load time.

  The inverse is NOT enforced — FLOOR_EXEMPT_TASK_IDS may contain IDs
  that are not in the current catalog (future tasks, removed tasks).

TaskRegistry is NOT thread-safe (designed for single-tick, single-thread use).
Stdlib only. No imports from maintenance_worker.core.engine (no circular deps).
"""
from __future__ import annotations

from pathlib import Path

from maintenance_worker.core.install_metadata import FLOOR_EXEMPT_TASK_IDS
from maintenance_worker.rules.parser import TaskCatalogEntry, load_task_catalog
from maintenance_worker.types.specs import TaskSpec


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TaskNotFoundError(KeyError):
    """Raised by get_task() when task_id is not registered."""


class UnauthorizedExemptionError(ValueError):
    """
    Raised when a catalog task claims dry_run_floor_exempt=True but
    its task_id is NOT in FLOOR_EXEMPT_TASK_IDS.

    This prevents catalog drift from silently widening the hardcoded
    frozenset. SCAFFOLD §4.
    """


# ---------------------------------------------------------------------------
# TaskRegistry
# ---------------------------------------------------------------------------


class TaskRegistry:
    """
    In-memory registry of TaskCatalogEntry objects loaded from a catalog.

    Construction:
      registry = TaskRegistry(entries)          # from pre-loaded entries
      registry = TaskRegistry.from_catalog(path, env=None)  # from YAML path

    Look-up:
      entry   = registry.get_task(task_id)       # raises TaskNotFoundError
      entries = registry.list_tasks()            # all, in registration order
      entries = registry.get_tasks_for_schedule("daily")  # filter by schedule
      paused  = registry.is_task_paused(task_id, pause_flag_dir)

    The registry validates dry_run_floor_exempt claims at construction time.
    """

    def __init__(self, entries: list[TaskCatalogEntry]) -> None:
        """
        Build registry from pre-loaded entries.

        Raises:
          UnauthorizedExemptionError: if any entry claims
            dry_run_floor_exempt=True but its task_id is not in
            FLOOR_EXEMPT_TASK_IDS.
        """
        self._entries: list[TaskCatalogEntry] = []
        self._by_id: dict[str, TaskCatalogEntry] = {}

        for entry in entries:
            self._register(entry)

    # ------------------------------------------------------------------
    # Class-method constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_catalog(
        cls,
        path: str | Path,
        env: dict[str, str] | None = None,
    ) -> "TaskRegistry":
        """
        Load a YAML task catalog and return a populated TaskRegistry.

        Delegates to load_task_catalog(); propagates its exceptions
        (CatalogSchemaError, DuplicateTaskIdError, FileNotFoundError).
        """
        entries = load_task_catalog(path, env=env)
        return cls(entries)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _register(self, entry: TaskCatalogEntry) -> None:
        """Add one entry, enforcing the dry_run_floor_exempt cross-check."""
        task_id = entry.spec.task_id

        # Cross-check: catalog claim must match hardcoded frozenset
        if entry.spec.dry_run_floor_exempt and task_id not in FLOOR_EXEMPT_TASK_IDS:
            raise UnauthorizedExemptionError(
                f"Task '{task_id}' claims dry_run_floor_exempt=True in the catalog "
                f"but is NOT in FLOOR_EXEMPT_TASK_IDS={sorted(FLOOR_EXEMPT_TASK_IDS)!r}. "
                "Update the hardcoded frozenset in install_metadata.py to add this task, "
                "or remove the dry_run_floor_exempt flag from the catalog."
            )

        self._entries.append(entry)
        self._by_id[task_id] = entry

    # ------------------------------------------------------------------
    # Public look-up API
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> TaskCatalogEntry:
        """
        Return the TaskCatalogEntry for task_id.

        Raises TaskNotFoundError if not registered.
        """
        try:
            return self._by_id[task_id]
        except KeyError:
            raise TaskNotFoundError(
                f"Task '{task_id}' is not registered. "
                f"Known tasks: {sorted(self._by_id)!r}"
            ) from None

    def list_tasks(self) -> list[TaskCatalogEntry]:
        """Return all registered entries in registration order."""
        return list(self._entries)

    def get_tasks_for_schedule(self, schedule: str) -> list[TaskCatalogEntry]:
        """
        Return entries whose spec.schedule equals schedule (case-sensitive).

        Example: get_tasks_for_schedule("daily") returns all daily tasks.
        """
        return [e for e in self._entries if e.spec.schedule == schedule]

    def is_task_paused(self, task_id: str, pause_flag_dir: Path) -> bool:
        """
        Return True if a pause flag file exists for task_id.

        Per TASK_CATALOG.yaml spec:
          A file at ${pause_flag_dir}/<task_id>.pause skips that task on
          every tick until the file is removed.

        Does NOT check whether task_id is registered — callers should
        call get_task() first if they want registration validation.
        """
        flag_file = pause_flag_dir / f"{task_id}.pause"
        return flag_file.exists()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def task_ids(self) -> list[str]:
        """Return a list of all registered task_ids in registration order."""
        return [e.spec.task_id for e in self._entries]

    def get_spec(self, task_id: str) -> TaskSpec:
        """Return only the TaskSpec for task_id (convenience shortcut)."""
        return self.get_task(task_id).spec

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._by_id

    def __repr__(self) -> str:
        return (
            f"TaskRegistry({len(self._entries)} tasks: "
            f"{self.task_ids()!r})"
        )
