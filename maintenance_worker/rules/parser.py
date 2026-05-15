# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.3)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
"""
rules/parser — YAML task catalog loader.

Entry point: load_task_catalog(path, env=None) -> list[TaskCatalogEntry]

TaskCatalogEntry wraps a TaskSpec (P5.0a frozen dataclass, 5 fields) plus
the full raw YAML task payload dict. The raw dict lets P5.4 fixtures consume
fields (config, safety, evidence_emit, etc.) without widening TaskSpec.

Deviation logged: TaskCatalogEntry vs bare TaskSpec — SCAFFOLD §3 specifies
TaskSpec only; brief leaves room for a wrapper; Fork 1 chose wrapper to avoid
premature field explosion on a frozen dataclass. SCAFFOLD §4 cross-check logic
runs inside TaskRegistry, not here.

Env-var resolution resolves ${REPO}, ${STATE_DIR}, ${EVIDENCE_DIR},
${YEAR}, ${QUARTER} from the supplied env dict (or os.environ).
Project-specific vars (e.g. ${PROJECT_REPO}) are left as-is — the parser
is project-agnostic and does NOT fail on unknown vars.

Schema contract:
  schema_version: 1 (FATAL on mismatch)
  tasks[].id: required str
  tasks[].schedule: required str
  tasks[].rule_source: used as description; falls back to id if absent
  tasks[].dry_run_floor_exempt: optional bool, default False
  All other task keys: tolerated, stored in raw

Stdlib + PyYAML only. No imports from maintenance_worker.core.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from maintenance_worker.types.specs import TaskSpec


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_SCHEMA_VERSION = 1

# Pattern matching ${VAR_NAME} substitution tokens.
_ENV_VAR_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")

# Core vars resolved from env; project vars pass through unchanged.
_CORE_VAR_KEYS = frozenset(
    {
        "REPO",
        "STATE_DIR",
        "EVIDENCE_DIR",
        "YEAR",
        "QUARTER",
    }
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CatalogSchemaError(ValueError):
    """Raised on fatal schema_version mismatch or structurally invalid catalog."""


class DuplicateTaskIdError(ValueError):
    """Raised when two tasks share the same id within one catalog."""


# ---------------------------------------------------------------------------
# TaskCatalogEntry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskCatalogEntry:
    """
    One task as loaded from the YAML catalog.

    spec: minimal TaskSpec frozen dataclass (5 fields, P5.0a shape).
    raw: full YAML task dict with env-vars expanded where resolvable.
         Keys include id, schedule, rule_source, dry_run, live_default,
         config, safety, evidence_emit, and any unknown keys tolerated.

    Consumers needing config/safety/evidence_emit read from raw.
    TaskRegistry validates spec against FLOOR_EXEMPT_TASK_IDS.

    Deviation note: SCAFFOLD §3 uses bare TaskSpec; this wrapper avoids
    widening the frozen TaskSpec with catalog-only fields.
    """

    spec: TaskSpec
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Env-var expansion
# ---------------------------------------------------------------------------


def _build_env_map(
    extra: dict[str, str] | None,
    now_year: int | None = None,
    now_quarter: int | None = None,
) -> dict[str, str]:
    """
    Build the resolution map for ${VAR} expansion.

    Core vars: REPO, STATE_DIR, EVIDENCE_DIR, YEAR, QUARTER.
    All other vars come from extra (caller-supplied) or os.environ.
    Precedence: extra > os.environ.
    """
    import datetime

    base: dict[str, str] = dict(os.environ)
    if extra:
        base.update(extra)

    # Compute YEAR and QUARTER if not already in env
    today = datetime.date.today()
    year = now_year if now_year is not None else today.year
    quarter = now_quarter if now_quarter is not None else (today.month - 1) // 3 + 1

    resolved: dict[str, str] = dict(base)
    resolved.setdefault("YEAR", str(year))
    resolved.setdefault("QUARTER", str(quarter))

    return resolved


def _expand_env_vars(value: str, env: dict[str, str]) -> str:
    """
    Replace ${VAR_NAME} tokens in value using env.

    Unknown vars (not present in env) are left as-is. This makes the
    parser project-agnostic — ${PROJECT_REPO} and similar project vars pass
    through unchanged.
    """

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        key = m.group(1)
        return env.get(key, m.group(0))  # keep original if unknown

    return _ENV_VAR_RE.sub(_replace, value)


def _expand_value(value: Any, env: dict[str, str]) -> Any:
    """Recursively expand ${VAR} tokens in strings within nested structures."""
    if isinstance(value, str):
        return _expand_env_vars(value, env)
    if isinstance(value, dict):
        return {k: _expand_value(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_value(item, env) for item in value]
    return value


def _expand_task_dict(task_dict: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Expand env vars in all string values within a task's YAML dict."""
    return {k: _expand_value(v, env) for k, v in task_dict.items()}


# ---------------------------------------------------------------------------
# Task dict → TaskCatalogEntry
# ---------------------------------------------------------------------------


def _task_dict_to_entry(task_dict: dict[str, Any], env: dict[str, str]) -> TaskCatalogEntry:
    """
    Convert one YAML task dict to a TaskCatalogEntry.

    Required fields: id (→ task_id), schedule.
    description falls back to rule_source, then task id.
    Unknown keys are tolerated (stored in raw, not TaskSpec).
    """
    task_id: str = task_dict.get("id", "")
    if not task_id:
        raise CatalogSchemaError("Task entry is missing required 'id' field.")

    schedule: str = task_dict.get("schedule", "")
    if not schedule:
        raise CatalogSchemaError(
            f"Task '{task_id}' is missing required 'schedule' field."
        )

    description: str = (
        task_dict.get("rule_source")
        or task_dict.get("description")
        or task_id
    )

    dry_run_floor_exempt: bool = bool(task_dict.get("dry_run_floor_exempt", False))

    # tags: extract from tags key if present; TASK_CATALOG.yaml doesn't use it
    # but we honour it for forward-compat
    raw_tags = task_dict.get("tags", [])
    if isinstance(raw_tags, list):
        tags: tuple[str, ...] = tuple(str(t) for t in raw_tags)
    else:
        tags = ()

    spec = TaskSpec(
        task_id=task_id,
        description=description,
        schedule=schedule,
        dry_run_floor_exempt=dry_run_floor_exempt,
        tags=tags,
    )

    # Expand env vars in the full raw dict for downstream consumers
    expanded_raw = _expand_task_dict(task_dict, env)

    return TaskCatalogEntry(spec=spec, raw=expanded_raw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_task_catalog(
    path: str | Path,
    env: dict[str, str] | None = None,
) -> list[TaskCatalogEntry]:
    """
    Load and validate a YAML task catalog.

    path: path to the YAML file (absolute or relative; converted to Path).
    env: optional supplemental env-var dict; merged with os.environ.
         Keys override os.environ; useful for injecting REPO, STATE_DIR
         etc. in tests without touching the real environment.

    Returns list[TaskCatalogEntry] in catalog order.

    Raises:
      FileNotFoundError: if path does not exist.
      CatalogSchemaError: on schema_version mismatch or structurally
                           invalid catalog.
      DuplicateTaskIdError: if two tasks share the same id.
      yaml.YAMLError: on YAML parse failure (propagated as-is).
    """
    catalog_path = Path(path)
    if not catalog_path.exists():
        raise FileNotFoundError(f"Task catalog not found: {catalog_path}")

    raw_text = catalog_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw_text)

    if not isinstance(data, dict):
        raise CatalogSchemaError(
            f"Task catalog must be a YAML mapping; got {type(data).__name__}."
        )

    # Schema version check — FATAL on mismatch
    schema_version = data.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise CatalogSchemaError(
            f"Task catalog schema_version={schema_version!r}, "
            f"expected {SUPPORTED_SCHEMA_VERSION}. "
            "Manual update required."
        )

    # Build env-var resolution map
    env_map = _build_env_map(env)

    # Parse tasks list
    tasks_raw: Any = data.get("tasks", [])
    if not isinstance(tasks_raw, list):
        raise CatalogSchemaError(
            f"'tasks' must be a YAML sequence; got {type(tasks_raw).__name__}."
        )

    entries: list[TaskCatalogEntry] = []
    seen_ids: set[str] = set()

    for i, task_dict in enumerate(tasks_raw):
        if not isinstance(task_dict, dict):
            raise CatalogSchemaError(
                f"tasks[{i}] must be a YAML mapping; got {type(task_dict).__name__}."
            )
        entry = _task_dict_to_entry(task_dict, env_map)
        task_id = entry.spec.task_id

        if task_id in seen_ids:
            raise DuplicateTaskIdError(
                f"Duplicate task_id '{task_id}' in catalog {catalog_path}."
            )
        seen_ids.add(task_id)
        entries.append(entry)

    return entries
