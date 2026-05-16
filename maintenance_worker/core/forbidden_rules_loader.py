# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md
#   §"Forbidden Targets" Groups 1–6
#   bindings/universal/safety_defaults.yaml (universal rules)
#   bindings/zeus/safety_overrides.yaml (project-specific additive rules)
"""
forbidden_rules_loader — Load forbidden-path rules from binding YAML files.

load_forbidden_rules(bindings_dir: Path) -> list[ForbiddenRule]

Reads:
  1. bindings/universal/safety_defaults.yaml  — REQUIRED; HARD FAIL if absent
  2. bindings/<project>/safety_overrides.yaml — UNION (additive only)

Rules from safety_defaults are prepended; safety_overrides are appended.
Missing universal-defaults → raises ConfigurationError (fail-closed).
Missing project overrides → WARNING logged; universal rules still apply.

Cached per-process per bindings_dir string (functools.cache).

Transition safety: if env var MW_FORBIDDEN_RULES_FROM_CODE=1 is set,
validator.py falls back to the original hardcoded _FORBIDDEN_RULES list.
That logic lives in validator.py, not here.
"""
from __future__ import annotations

import logging
import os
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when a required configuration file is absent or malformed."""


# ---------------------------------------------------------------------------
# Public API — ForbiddenRule is imported lazily to avoid circular imports.
# The ForbiddenRule dataclass lives in validator.py; loader returns them
# via the factory below.
# ---------------------------------------------------------------------------

def _make_rule(pattern: str, group: str, description: str,
               prefix: bool = False, exact_name: bool = False) -> object:
    """
    Construct a ForbiddenRule via lazy import from validator.

    Lazy import breaks the potential circular-import cycle:
      validator imports forbidden_rules_loader at function call time,
      forbidden_rules_loader imports validator only at rule-construction time,
      which happens only when load_forbidden_rules() is called (after both
      modules are fully loaded).
    """
    from maintenance_worker.core.validator import ForbiddenRule  # noqa: PLC0415
    return ForbiddenRule(
        pattern=pattern,
        group=group,
        description=description,
        prefix=prefix,
        exact_name=exact_name,
    )


def _parse_entries(entries: list[dict], source_label: str) -> list[object]:
    """
    Parse a list of YAML rule dicts into ForbiddenRule objects.

    Skips entries missing required fields with a WARNING.
    """
    rules = []
    for i, entry in enumerate(entries):
        pattern = entry.get("pattern", "")
        group = entry.get("group", "")
        description = entry.get("description", "")
        if not pattern or not group:
            logger.warning(
                "forbidden_rules_loader: skipping entry #%d in %s — "
                "missing required field 'pattern' or 'group'",
                i, source_label,
            )
            continue
        prefix = bool(entry.get("prefix", False))
        exact_name = bool(entry.get("exact_name", False))
        rules.append(_make_rule(pattern, group, str(description).strip(), prefix, exact_name))
    return rules


def _load_yaml_entries(path: Path, required: bool, list_key: str) -> list[dict]:
    """
    Load a YAML file and return the list at list_key.

    If required=True and file absent/malformed → raises ConfigurationError.
    If required=False and file absent → returns [] with WARNING.
    """
    if not path.exists():
        if required:
            raise ConfigurationError(
                f"Required forbidden-rules file not found: {path}. "
                "This is a HARD FAILURE — maintenance_worker cannot run safely "
                "without universal safety defaults. Check bindings directory."
            )
        logger.warning(
            "forbidden_rules_loader: optional overrides file not found at %s; "
            "universal rules still apply.",
            path,
        )
        return []

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        if required:
            raise ConfigurationError(
                f"Failed to parse required forbidden-rules file {path}: {exc}"
            ) from exc
        logger.warning(
            "forbidden_rules_loader: failed to parse %s: %s; skipping.",
            path, exc,
        )
        return []

    if not isinstance(data, dict):
        if required:
            raise ConfigurationError(
                f"Required forbidden-rules file {path} has unexpected structure "
                f"(expected dict, got {type(data).__name__})."
            )
        return []

    entries = data.get(list_key, []) or []
    if not isinstance(entries, list):
        return []
    return entries


@cache
def load_forbidden_rules(bindings_dir: str) -> list:
    """
    Load and return the merged forbidden-rule list for the given bindings directory.

    Args:
        bindings_dir: string path to the bindings/ root directory
                      (keyed as string for functools.cache hashability).

    Returns:
        list[ForbiddenRule] — universal defaults prepended, project overrides appended.

    Raises:
        ConfigurationError: if universal defaults file is absent or malformed.
    """
    base = Path(bindings_dir)

    # 1. Universal defaults — REQUIRED (fail-closed)
    universal_path = base / "universal" / "safety_defaults.yaml"
    universal_entries = _load_yaml_entries(universal_path, required=True, list_key="forbidden_paths")
    universal_rules = _parse_entries(universal_entries, str(universal_path))

    # 2. Project overrides — best-effort (optional; missing → WARNING only)
    # Discover project subdirectory: any non-"universal" subdir containing safety_overrides.yaml
    project_rules: list[object] = []
    try:
        for subdir in sorted(base.iterdir()):
            if not subdir.is_dir() or subdir.name == "universal":
                continue
            overrides_path = subdir / "safety_overrides.yaml"
            if overrides_path.exists():
                override_entries = _load_yaml_entries(
                    overrides_path, required=False, list_key="additional_forbidden_paths"
                )
                project_rules.extend(_parse_entries(override_entries, str(overrides_path)))
    except OSError as exc:
        logger.warning(
            "forbidden_rules_loader: error scanning bindings subdirectories: %s", exc
        )

    merged = universal_rules + project_rules
    logger.debug(
        "forbidden_rules_loader: loaded %d universal + %d project rules from %s",
        len(universal_rules), len(project_rules), bindings_dir,
    )
    return merged
