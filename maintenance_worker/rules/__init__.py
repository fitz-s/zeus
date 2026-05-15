# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.3)
"""
maintenance_worker.rules — task catalog parsing and registry.

Public API:
  from maintenance_worker.rules.parser import load_task_catalog, TaskCatalogEntry
  from maintenance_worker.rules.task_registry import TaskRegistry
"""
from __future__ import annotations
