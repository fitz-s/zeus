# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.3
"""
Loads and validates the Zeus binding-layer YAML into typed BindingLayer.

Public API:
    load_binding_layer(path) -> BindingLayer
    validate_binding_layer(bl) -> list[str]

Properties:
- Single source path; no auto-discovery, no merging.
- Raises FileNotFoundError naming the path when absent.
- Reports unknown fields without crashing (warn-don't-crash contract).
- Codex-importable: no Claude-Code-specific imports, no env-var dependencies.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import yaml

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CohortDecl,
    CoverageMap,
    Intent,
    Severity,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_binding_layer(path: str | Path = "architecture/topology_v_next_binding.yaml") -> BindingLayer:
    """
    Load and parse the binding YAML at *path* into a typed BindingLayer.

    Raises FileNotFoundError with a message naming *path* when the file does
    not exist (SCAFFOLD §1.3 m1 minor: load order documented here, not hidden
    in admission_engine).

    Unknown top-level YAML keys are silently tolerated (warn-don't-crash per
    §1.3). Unknown intent_extension IDs (strings not in the Intent enum) are
    dropped and a UserWarning is emitted for each (d2 fix). Call
    validate_binding_layer() to surface structural warnings after loading.
    """
    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(
            f"topology_v_next binding YAML not found: {resolved}. "
            "Expected at 'architecture/topology_v_next_binding.yaml' or the path supplied."
        )

    with resolved.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    bl, dropped_intents = _parse_binding_layer(raw)

    for id_value in dropped_intents:
        warnings.warn(
            f"topology_v_next binding: intent_extension id '{id_value}' is not a "
            "known Intent enum value and was dropped. Add it to the Intent enum or "
            "remove it from the binding YAML.",
            UserWarning,
            stacklevel=2,
        )

    return bl


def validate_binding_layer(bl: BindingLayer) -> list[str]:
    """
    Return a list of warning strings for gaps or policy violations in *bl*.

    Does NOT raise. Returns empty list when clean.

    Checks:
    - intent_extensions missing 'zeus.' namespace prefix
    - Empty coverage_map.profiles
    - artifact_authority_status rows with missing required sub-keys
    """
    warnings: list[str] = []

    # Check intent extensions have project namespace prefix
    for ext in bl.intent_extensions:
        value: str = ext.value
        if "." not in value:
            warnings.append(
                f"intent_extension '{value}' has no namespace prefix "
                "(expected '<project>.<name>'). Universal namespace collision risk."
            )

    # Warn if no profiles declared
    if not bl.coverage_map.profiles:
        warnings.append("coverage_map.profiles is empty; all files will be coverage gaps.")

    # Warn on artifact_authority_status rows missing required sub-keys
    required_status_keys = {"status", "last_confirmed", "confirmation_ttl_days"}
    for artifact_path, row in bl.artifact_authority_status.items():
        missing = required_status_keys - set(row.keys())
        if missing:
            warnings.append(
                f"artifact_authority_status['{artifact_path}'] missing keys: "
                f"{sorted(missing)}. TTL freshness enforcement will be incomplete."
            )

    return warnings


# ---------------------------------------------------------------------------
# Internal parsing helpers
# ---------------------------------------------------------------------------

def _parse_binding_layer(raw: dict[str, Any]) -> tuple[BindingLayer, list[str]]:
    """
    Convert raw YAML dict to BindingLayer.

    Unknown top-level keys are ignored (warn-don't-crash per §1.3).
    Returns (BindingLayer, dropped_intent_ids) so the caller can warn on drops.
    """
    project_id: str = raw.get("project_id", "")

    intent_extensions, dropped_intents = _parse_intent_extensions(
        raw.get("intent_extensions") or []
    )
    coverage_map = _parse_coverage_map(raw.get("coverage_map") or {})
    cohorts = _parse_cohorts(raw.get("cohorts") or [])
    severity_overrides = _parse_severity_overrides(raw.get("severity_overrides") or {})
    high_fanout_hints = tuple(raw.get("high_fanout_hints") or [])
    artifact_authority_status = _parse_artifact_authority_status(
        raw.get("artifact_authority_status") or {}
    )

    bl = BindingLayer(
        project_id=project_id,
        intent_extensions=intent_extensions,
        coverage_map=coverage_map,
        cohorts=cohorts,
        severity_overrides=severity_overrides,
        high_fanout_hints=high_fanout_hints,
        artifact_authority_status=artifact_authority_status,
    )
    return bl, dropped_intents


def _parse_intent_extensions(
    raw_list: list[dict[str, Any]],
) -> tuple[tuple[Intent, ...], list[str]]:
    """
    Parse intent_extensions from YAML list.

    Each entry must have an 'id' key matching a value in the Intent enum.
    Unknown IDs are collected in ``dropped`` and returned to the caller so
    a UserWarning can be emitted (d2 fix: silent-drop → warn on unknown IDs).

    Returns (parsed_extensions_tuple, dropped_id_strings).
    """
    result: list[Intent] = []
    dropped: list[str] = []
    for entry in raw_list:
        id_value: str = entry.get("id", "")
        try:
            result.append(Intent(id_value))
        except ValueError:
            # Unknown intent value: not in enum yet. Collect for caller warning.
            if id_value:
                dropped.append(id_value)
    return tuple(result), dropped


def _parse_coverage_map(raw: dict[str, Any]) -> CoverageMap:
    """Parse coverage_map section."""
    profiles_raw: list[dict[str, Any]] = raw.get("profiles") or []
    profiles: dict[str, tuple[str, ...]] = {}
    for profile_entry in profiles_raw:
        profile_id: str = profile_entry.get("id", "")
        patterns: tuple[str, ...] = tuple(profile_entry.get("patterns") or [])
        if profile_id:
            profiles[profile_id] = patterns

    orphaned: tuple[str, ...] = tuple(raw.get("orphaned") or [])
    hard_stop_paths: tuple[str, ...] = tuple(raw.get("hard_stop_paths") or [])

    return CoverageMap(
        profiles=profiles,
        orphaned=orphaned,
        hard_stop_paths=hard_stop_paths,
    )


def _parse_cohorts(raw_list: list[dict[str, Any]]) -> tuple[CohortDecl, ...]:
    """Parse cohorts list."""
    result: list[CohortDecl] = []
    for entry in raw_list:
        cohort_id: str = entry.get("id", "")
        profile: str = entry.get("profile", "")
        intent_classes_raw: list[str] = entry.get("intent_classes") or []
        files_raw: list[str] = entry.get("files") or []
        description: str = entry.get("description", "")

        # Resolve intent_classes; skip unknown values
        intent_classes: list[Intent] = []
        for ic_str in intent_classes_raw:
            try:
                intent_classes.append(Intent(ic_str))
            except ValueError:
                pass

        if cohort_id and profile:
            result.append(CohortDecl(
                id=cohort_id,
                profile=profile,
                intent_classes=tuple(intent_classes),
                files=tuple(files_raw),
                description=description,
            ))
    return tuple(result)


def _parse_severity_overrides(raw: dict[str, Any]) -> dict[str, Severity]:
    """
    Parse severity_overrides map.

    Unknown severity values are skipped (warn-don't-crash per §1.3).
    """
    result: dict[str, Severity] = {}
    for code, sev_str in raw.items():
        try:
            result[code] = Severity(sev_str)
        except ValueError:
            pass
    return result


def _parse_artifact_authority_status(
    raw: dict[str, Any] | list[Any],
) -> dict[str, dict[str, Any]]:
    """
    Parse artifact_authority_status.

    YAML_BINDING_LAYER §8 defines this as a list of rows; we normalise to a
    dict keyed by 'path' for O(1) lookup in admission_engine._check_authority_status.
    Also accepts a plain dict (for stub YAML that uses empty dict {}).
    """
    if isinstance(raw, dict):
        # Stub case: empty dict or already-keyed dict
        return dict(raw)

    result: dict[str, dict[str, Any]] = {}
    for row in raw:
        path_key: str = row.get("path", "")
        if path_key:
            result[path_key] = {k: v for k, v in row.items() if k != "path"}
    return result
