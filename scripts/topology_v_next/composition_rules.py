# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.8
#                  docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §3.0
"""
Composition Rules for topology v_next admission system.

Implements Universal §7 Rules C1–C4 and §8 Cohort Admission.
§9 (companion loop break) is a thin shim in companion_loop_break.py
that delegates back here.

Anti-sidecar (SCAFFOLD §5.4):
- hint/phrase is NOT a parameter to cohort_admit() or apply_composition().
- hint appears ONLY in explain_rejected() as a ranking signal for diagnostics.
- Phrase never gates routing or influences profile selection.

Public:
    cohort_admit(intent, files, cohorts) -> CohortDecl | None
    apply_composition(intent, files, candidates, binding) -> tuple[str | None, list[IssueRecord]]
    explain_rejected(candidates, binding, hint) -> str | None

Codex-importable: stdlib + PyYAML only.
"""
from __future__ import annotations

import fnmatch

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CohortDecl,
    Intent,
    IssueRecord,
    Severity,
)
from scripts.topology_v_next.coverage_map import union_candidate_profiles


def cohort_admit(
    intent: Intent,
    files: list[str],
    cohorts: tuple[CohortDecl, ...],
) -> CohortDecl | None:
    """
    Return the first matching cohort or None.

    Match condition (Universal §8):
    - intent is in cohort.intent_classes
    - ALL files in cohort.files (after glob/template expansion) are present
      in the submitted files list.

    Template expansion: '{new_module}' patterns in cohort.files are treated
    as wildcards matching any single path component (fnmatch '*' substitution).
    This lets a cohort like 'tests/test_{new_module}.py' match any new test
    file alongside its companion 'architecture/test_topology.yaml'.

    Phrase/hint is NOT a parameter (SCAFFOLD §5.4).
    """
    files_set = set(files)

    for cohort in cohorts:
        if intent not in cohort.intent_classes:
            continue
        if _all_cohort_files_present(cohort.files, files_set):
            return cohort

    return None


def apply_composition(
    intent: Intent,
    files: list[str],
    candidates: dict[str, set[str]],
    binding: BindingLayer,
) -> tuple[str | None, list[IssueRecord]]:
    """
    Apply Universal §7 composition rules C1–C4 to resolve a single profile.

    Returns (resolved_profile_id_or_None, issues).

    Rules tried in order:
    C1: Additive companion — one of the files extends an existing profile;
        if all files are covered by that same profile → admit under it.
    C2: Subsumption — all files map to subsets of one profile → that profile.
    C3: Explicit union profile — binding declares a union profile covering
        the exact set of profiles touched (not implemented in stub binding;
        returns None if no union profile declared).
    C4: Cohort — binding.cohorts declares an explicit multi-profile cohort
        for this intent + file set → admit under the cohort's profile.

    When nothing resolves: emits composition_conflict SOFT_BLOCK.

    Phrase/hint is NOT a parameter (SCAFFOLD §5.4).
    """
    issues: list[IssueRecord] = []

    # §3.0 companion_required pre-registration (P2.1 SCAFFOLD §3.0):
    # For each profile that has companion_required paths, if the submitted files
    # contain a source file already assigned to that profile, pre-register every
    # companion_required path as also belonging to that same profile.
    # This prevents the unsolvable trap: adding the required companion doc would
    # otherwise expand touched_profiles to ≥2, causing composition_conflict
    # BEFORE _check_companion_required ever runs (SCAFFOLD §3.0).
    candidates = _preregister_companion_paths(files, candidates, binding)

    touched_profiles = union_candidate_profiles(candidates)

    # C1 / C2: single-profile resolution (subsumption or additive companion)
    if len(touched_profiles) == 1:
        profile_id = next(iter(touched_profiles))
        return profile_id, issues

    # C3: explicit union profile (stub binding has none; structural placeholder)
    union_profile = _find_union_profile(touched_profiles, binding)
    if union_profile is not None:
        return union_profile, issues

    # C4: cohort
    cohort = cohort_admit(intent, files, binding.cohorts)
    if cohort is not None:
        return cohort.profile, issues

    # Nothing resolved
    if touched_profiles:
        issues.append(IssueRecord(
            code="composition_conflict",
            path="",
            severity=Severity.SOFT_BLOCK,
            message=(
                f"Files touch {len(touched_profiles)} profiles "
                f"({', '.join(sorted(touched_profiles))}) with no composition "
                "rule resolving them to a single profile. Declare a cohort in "
                "the binding YAML or add an explicit union profile."
            ),
            metadata={"touched_profiles": sorted(touched_profiles)},
        ))
    else:
        # All files are coverage gaps — no profiles touched
        issues.append(IssueRecord(
            code="composition_conflict",
            path="",
            severity=Severity.SOFT_BLOCK,
            message=(
                "No files match any profile in the binding layer. "
                "All submitted files are coverage gaps."
            ),
            metadata={"touched_profiles": []},
        ))

    return None, issues


def explain_rejected(
    candidates: dict[str, set[str]],
    binding: BindingLayer,
    hint: str,
) -> str | None:
    """
    Return the closest rejected profile for diagnostic display, or None.

    The hint string is used ONLY HERE to rank candidate profiles by relevance.
    It NEVER gates routing or influences profile selection (SCAFFOLD §5.4).
    This function is called by admission_engine after the routing decision
    is already made, purely to populate AdmissionDecision.closest_rejected_profile.
    """
    touched = union_candidate_profiles(candidates)
    if not touched:
        return None

    # When exactly one profile was touched but composition failed (multi-file
    # across profiles), we'd have resolved it above. Here we rank by hint proximity.
    # Simple heuristic: profile whose id is a substring of hint, else first sorted.
    hint_lower = hint.lower()
    candidates_list = sorted(touched)

    for profile_id in candidates_list:
        if profile_id.lower() in hint_lower:
            return profile_id

    # Fall back to first alphabetically
    return candidates_list[0] if candidates_list else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _all_cohort_files_present(
    cohort_files: tuple[str, ...],
    submitted: set[str],
) -> bool:
    """
    Return True if every cohort_file pattern matches at least one submitted file.

    Handles '{new_module}' template substitution by replacing with '*' for
    fnmatch matching. A literal path without templates is checked directly.
    """
    for cohort_pattern in cohort_files:
        # Expand template placeholders to fnmatch wildcards
        fnmatch_pattern = cohort_pattern.replace("{new_module}", "*")
        if not any(fnmatch.fnmatch(f, fnmatch_pattern) for f in submitted):
            return False
    return True


def _preregister_companion_paths(
    files: list[str],
    candidates: dict[str, set[str]],
    binding: BindingLayer,
) -> dict[str, set[str]]:
    """
    Pre-register companion_required paths as Rule C1 declared companions.

    For each profile_id that has companion_required entries:
    1. Check if any submitted file is already assigned to that profile_id.
    2. If yes, add every companion_required path to that file's candidate set
       (or create a new candidates entry for those companion paths pointing
       to the same profile_id).

    This ensures that submitting (source_file + companion_doc) does NOT expand
    touched_profiles beyond 1, preventing the composition_conflict trap described
    in SCAFFOLD §3.0.

    Returns a new candidates dict (no mutation of the original).
    """
    if not binding.companion_required:
        return candidates

    # Determine which profiles are already touched by the submitted files
    # (pre-preregistration — only source files, not companion docs)
    profiles_in_source: set[str] = set()
    for profile_candidates in candidates.values():
        profiles_in_source.update(profile_candidates)

    if not profiles_in_source:
        return candidates

    # Build updated candidates dict; start with a shallow copy
    updated: dict[str, set[str]] = {f: set(profiles) for f, profiles in candidates.items()}

    for profile_id, companion_paths in binding.companion_required.items():
        if profile_id not in profiles_in_source:
            continue  # No source file from this profile in the change set; skip.

        for companion_path in companion_paths:
            if companion_path in updated:
                # File already in candidates — add this profile to its set
                updated[companion_path].add(profile_id)
            else:
                # Companion doc not yet in candidates (i.e. not a source file)
                # Create a new entry so composition sees it as belonging to profile_id
                updated[companion_path] = {profile_id}

    return updated


def _find_union_profile(
    touched_profiles: set[str],
    binding: BindingLayer,
) -> str | None:
    """
    Look for an explicit union profile in the binding layer that subsumes
    exactly the set of touched profiles.

    The stub binding has no union profiles; this returns None for all P1
    invocations. The structure is here for P1.3 binding expansion.

    Union profile detection convention: a profile whose id ends with
    '_union' and whose pattern set covers all touched profiles. For P1,
    this is a structural placeholder only.
    """
    # No union profile mechanism in stub binding; return None
    # P1.3 or later may populate this via a binding-layer 'union_profiles' key.
    _ = touched_profiles  # used by future implementation
    _ = binding
    return None
