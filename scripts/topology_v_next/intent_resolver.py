# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.4, §5.3, §5.4
"""
Intent resolver for topology v_next admission system.

CRITICAL ANTI-SIDECAR PROPERTY (SCAFFOLD §5.3 / §5.4):
- This module does NOT derive intent from any task phrase or free text.
- Intent is supplied by the caller as a typed Intent enum value or a string
  that resolves to one.
- The "resolver" only validates and normalises the caller-supplied value.
- There is no `derive_intent_from_phrase`, `infer_intent`, or `guess_intent`
  function in this module. Any such addition is a sidecar (FAIL per §5.4).

Public:
    resolve_intent(intent_value, *, binding) -> tuple[Intent, list[IssueRecord]]
    is_zeus_intent(intent) -> bool

Codex-importable: no Claude-Code-specific imports, no env-var dependencies.
"""
from __future__ import annotations

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    Intent,
    IssueRecord,
    Severity,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_intent(
    intent_value: str | Intent | None,
    *,
    binding: BindingLayer,
) -> tuple[Intent, list[IssueRecord]]:
    """
    Validate and normalise a caller-supplied intent value.

    Parameters
    ----------
    intent_value:
        A typed Intent enum member, a string matching an Intent enum value
        (universal or zeus.* extension), or None.
    binding:
        The loaded BindingLayer (used to check zeus.* extension validity).

    Returns
    -------
    (resolved_intent, issues)
        resolved_intent — the normalised Intent enum member.
        issues — list of IssueRecord; empty when intent was clean.

    Issue codes emitted:
    - ``intent_unspecified`` ADVISORY — when intent_value is None.
    - ``intent_enum_unknown`` ADVISORY — when a string does not match any
      canonical or extension Intent value; resolution falls back to Intent.other.

    Phrase / task text is NEVER an input here. See §5.4 anti-pattern catch list.
    """
    issues: list[IssueRecord] = []

    if intent_value is None:
        issues.append(IssueRecord(
            code="intent_unspecified",
            path="",
            severity=Severity.ADVISORY,
            message=(
                "No intent supplied. Defaulting to Intent.other. "
                "Supply a typed intent value for deterministic routing."
            ),
        ))
        return Intent.other, issues

    # Already a typed Intent — validate it's recognised (incl. extensions)
    if isinstance(intent_value, Intent):
        _check_extension_registered(intent_value, binding, issues)
        return intent_value, issues

    # String resolution: attempt to coerce to Intent enum
    if isinstance(intent_value, str):
        try:
            resolved = Intent(intent_value)
        except ValueError:
            issues.append(IssueRecord(
                code="intent_enum_unknown",
                path="",
                severity=Severity.ADVISORY,
                message=(
                    f"Intent string '{intent_value}' does not match any canonical "
                    "or extension Intent value. Falling back to Intent.other. "
                    "Add the value to intent_extensions in the binding YAML if it "
                    "is a valid project intent."
                ),
            ))
            return Intent.other, issues
        _check_extension_registered(resolved, binding, issues)
        return resolved, issues

    # Unexpected type: treat as unspecified
    issues.append(IssueRecord(
        code="intent_unspecified",
        path="",
        severity=Severity.ADVISORY,
        message=(
            f"Intent value has unexpected type {type(intent_value).__name__!r}. "
            "Defaulting to Intent.other."
        ),
    ))
    return Intent.other, issues


def is_zeus_intent(intent: Intent) -> bool:
    """Return True if *intent* is a Zeus-namespace extension (value starts with 'zeus.')."""
    return intent.value.startswith("zeus.")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_extension_registered(
    intent: Intent,
    binding: BindingLayer,
    issues: list[IssueRecord],
) -> None:
    """
    Emit an ADVISORY if a zeus.* intent is not declared in binding.intent_extensions.

    Universal intents are always valid and need no registration check.
    """
    if not is_zeus_intent(intent):
        return  # universal intents always valid

    if intent not in binding.intent_extensions:
        issues.append(IssueRecord(
            code="intent_extension_unregistered",
            path="",
            severity=Severity.ADVISORY,
            message=(
                f"Zeus intent '{intent.value}' is not registered in the binding "
                "layer's intent_extensions. This may indicate a stale binding YAML. "
                "Add the intent to architecture/topology_v_next_binding.yaml."
            ),
        ))
