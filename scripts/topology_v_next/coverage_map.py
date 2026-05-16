# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.7
"""
Coverage Map resolver for topology v_next admission system.

Resolves files to candidate profiles via Coverage Map (Universal §6).
All three public functions are phrase-free — hint never enters here
(SCAFFOLD §5.4 anti-pattern catch list).

Public:
    resolve_candidates(files, coverage_map) -> dict[str, set[str]]
    coverage_gaps(candidates, coverage_map) -> list[IssueRecord]
    union_candidate_profiles(candidates) -> set[str]

Codex-importable: stdlib + PyYAML only.
"""
from __future__ import annotations

import fnmatch

from scripts.topology_v_next.dataclasses import (
    CoverageMap,
    IssueRecord,
    Severity,
)


def resolve_candidates(
    files: list[str],
    coverage_map: CoverageMap,
) -> dict[str, set[str]]:
    """
    Return {file_path: {profile_id, ...}} for each file in *files*.

    An empty set means the file matches no profile patterns.
    Does NOT consult orphaned or hard_stop_paths — those are handled
    by the caller (admission_engine) for gap reporting and kernel checks.

    Phrase / hint is NOT a parameter (SCAFFOLD §5.4).
    """
    result: dict[str, set[str]] = {}
    for file_path in files:
        matched: set[str] = set()
        for profile_id, patterns in coverage_map.profiles.items():
            for pattern in patterns:
                if _fnmatch_path(file_path, pattern):
                    matched.add(profile_id)
                    break  # profile matched; no need to check more patterns
        result[file_path] = matched
    return result


def coverage_gaps(
    candidates: dict[str, set[str]],
    coverage_map: CoverageMap,
) -> list[IssueRecord]:
    """
    Emit a coverage_gap ADVISORY for each file in *candidates* that:
    - maps to an empty candidate set (no profile patterns matched), AND
    - is not in coverage_map.orphaned, AND
    - is not in coverage_map.hard_stop_paths.

    Files in orphaned or hard_stop_paths are intentionally unprofilable;
    their absence from profiles is not a gap.
    """
    issues: list[IssueRecord] = []
    orphaned = coverage_map.orphaned
    hard_stops = coverage_map.hard_stop_paths

    for file_path, profiles in candidates.items():
        if profiles:
            continue  # has at least one candidate profile → not a gap

        # Check if file is in orphaned or hard_stop_paths (exempt from gap)
        if _matches_any(file_path, orphaned) or _matches_any(file_path, hard_stops):
            continue

        issues.append(IssueRecord(
            code="coverage_gap",
            path=file_path,
            severity=Severity.ADVISORY,
            message=(
                f"File '{file_path}' is not covered by any profile pattern, "
                "is not in the orphaned list, and is not a hard_stop path. "
                "Add a profile pattern or declare it orphaned in the binding YAML."
            ),
            metadata={"file": file_path},
        ))

    return issues


def union_candidate_profiles(candidates: dict[str, set[str]]) -> set[str]:
    """
    Collapse per-file candidate sets into the union of all profiles touched.

    Returns the full set of profile IDs that cover at least one file in the
    change set. Empty set means no file matched any profile.
    """
    result: set[str] = set()
    for profiles in candidates.values():
        result |= profiles
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fnmatch_path(file_path: str, pattern: str) -> bool:
    """
    Match *file_path* against *pattern* using fnmatch glob semantics.

    Handles '/**' suffix patterns for directory tree matching.
    """
    if fnmatch.fnmatch(file_path, pattern):
        return True

    # 'dir/**' → match any file under that directory
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        if file_path == prefix or file_path.startswith(prefix + "/"):
            return True

    return False


def _matches_any(file_path: str, patterns: tuple[str, ...]) -> bool:
    """Return True if *file_path* matches any pattern in *patterns*."""
    return any(_fnmatch_path(file_path, p) for p in patterns)
