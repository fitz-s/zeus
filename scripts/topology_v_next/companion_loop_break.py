# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.9
"""
Companion Loop Break — thin shim per Universal §9.

Delegates to composition_rules.cohort_admit() for 2-file companion pairs.
INCONSISTENCY-2 resolution: ONE mechanism (cohort_admit), two failure modes
(Mode A: companion present → auto-admit; Mode B: companion absent → SOFT_BLOCK).

In P1: SOFT_BLOCK is logged in AdmissionDecision.issues only — not enforced
at a gate (no gate exists until P2 packet wires the shim).

Public:
    companion_loop_break(intent, files, binding)
        -> tuple[bool, str | None, IssueRecord | None]

Codex-importable: stdlib + PyYAML only.
"""
from __future__ import annotations

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CohortDecl,
    Intent,
    IssueRecord,
    Severity,
)
from scripts.topology_v_next.composition_rules import cohort_admit


def companion_loop_break(
    intent: Intent,
    files: list[str],
    binding: BindingLayer,
) -> tuple[bool, str | None, IssueRecord | None]:
    """
    Check companion-pair cohort declarations for the submitted files.

    Iterates over 2-file cohorts in binding.cohorts. For each:

    Mode A (companion present):
        All required companion files are in *files* AND intent matches →
        return (True, None, None). Auto-admit: the companion pair is complete.

    Mode B (companion absent):
        Intent matches the cohort but at least one required companion file
        is missing from *files* → return (False, missing_path, issue).
        Issue is SOFT_BLOCK (P1: logged only, not enforced at gate).

    Returns (False, None, None) when no 2-file cohort applies to the intent
    and files combination (no companion loop detected).

    Delegates to cohort_admit() — ONE mechanism, no second code path.
    """
    files_set = set(files)
    two_file_cohorts = [c for c in binding.cohorts if len(c.files) == 2]

    for cohort in two_file_cohorts:
        if intent not in cohort.intent_classes:
            continue

        # Check if at least one submitted file matches one of the cohort patterns
        # (i.e., this cohort is "relevant" for the current change set)
        if not _any_file_touches_cohort(cohort, files_set):
            continue

        # Delegate to cohort_admit for the full check
        matched = cohort_admit(intent, files, (cohort,))
        if matched is not None:
            # Mode A: companion pair complete
            return True, None, None

        # Mode B: relevant cohort but companion missing
        missing = _find_missing_companion(cohort, files_set)
        issue = IssueRecord(
            code="companion_missing",
            path=missing or "",
            severity=Severity.SOFT_BLOCK,
            message=(
                f"Cohort '{cohort.id}' requires a companion file that is absent "
                f"from the submitted change set. "
                f"Missing: '{missing}'. "
                "Add the companion file or declare a cohort override. "
                "(P1: logged only; gate enforcement deferred to P2 packet.)"
            ),
            metadata={
                "cohort_id": cohort.id,
                "cohort_profile": cohort.profile,
                "missing_companion": missing or "",
            },
        )
        return False, missing, issue

    # No companion loop detected for this intent + files combination
    return False, None, None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _any_file_touches_cohort(cohort: CohortDecl, files_set: set[str]) -> bool:
    """
    Return True if any submitted file matches any pattern in cohort.files.

    Used to determine whether the cohort is "relevant" before checking
    whether the full companion set is present.
    """
    import fnmatch
    for pattern in cohort.files:
        fnmatch_pattern = pattern.replace("{new_module}", "*")
        if any(fnmatch.fnmatch(f, fnmatch_pattern) for f in files_set):
            return True
    return False


def _find_missing_companion(cohort: CohortDecl, files_set: set[str]) -> str | None:
    """
    Return the first cohort file pattern that matches nothing in files_set.

    This is the "missing companion" reported in Mode B. Returns None if all
    cohort files are matched (should not happen — caller checked cohort_admit
    returned None, meaning at least one is missing).
    """
    import fnmatch
    for pattern in cohort.files:
        fnmatch_pattern = pattern.replace("{new_module}", "*")
        if not any(fnmatch.fnmatch(f, fnmatch_pattern) for f in files_set):
            return pattern  # return the cohort pattern (most useful for diagnosis)
    return None
