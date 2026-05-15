# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.10, §8 P1.3
"""
Severity override application for topology v_next.

Extracted from admission_engine.py as a standalone module per P1.3 deliverable A.
Applies ZEUS_BINDING_LAYER §4 severity override table to the candidate issue list.

Public API:
    apply_overrides(issues, overrides) -> list[IssueRecord]
    effective_severity(issues) -> Severity

Properties:
- No mutation: apply_overrides returns new IssueRecord instances.
- effective_severity returns ADMIT for empty issue list.
- Codex-importable: stdlib only.
"""
from __future__ import annotations

from scripts.topology_v_next.dataclasses import IssueRecord, Severity


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_overrides(
    issues: list[IssueRecord],
    overrides: dict[str, Severity],
) -> list[IssueRecord]:
    """
    Apply binding severity_overrides to the issue list.

    Returns a new list with severities remapped per override dict.
    No mutation: issues that need remapping get new IssueRecord instances;
    unchanged issues are returned as-is (frozen dataclass — safe to share).

    Parameters
    ----------
    issues:
        List of IssueRecord from the admission pipeline.
    overrides:
        Mapping of issue code -> target Severity from binding.severity_overrides.
        Empty dict is a no-op (original list returned directly).

    Returns
    -------
    New list[IssueRecord] with severities remapped. Same length as input.
    """
    if not overrides:
        return issues

    result: list[IssueRecord] = []
    for issue in issues:
        new_sev = overrides.get(issue.code)
        if new_sev is not None and new_sev != issue.severity:
            result.append(IssueRecord(
                code=issue.code,
                path=issue.path,
                severity=new_sev,
                message=issue.message,
                metadata=issue.metadata,
            ))
        else:
            result.append(issue)
    return result


def effective_severity(issues: list[IssueRecord]) -> Severity:
    """
    Return the maximum severity across all issues.

    Ordering: HARD_STOP > SOFT_BLOCK > ADVISORY > ADMIT.
    Returns Severity.ADMIT for an empty issue list.

    Parameters
    ----------
    issues:
        List of IssueRecord to reduce.

    Returns
    -------
    The highest Severity value present, or Severity.ADMIT when issues is empty.
    """
    if not issues:
        return Severity.ADMIT

    _sev_order: dict[Severity, int] = {
        Severity.ADMIT: 0,
        Severity.ADVISORY: 1,
        Severity.SOFT_BLOCK: 2,
        Severity.HARD_STOP: 3,
    }
    return max(issues, key=lambda i: _sev_order.get(i.severity, 0)).severity
