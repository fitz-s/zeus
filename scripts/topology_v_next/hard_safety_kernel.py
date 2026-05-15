# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.6
"""
Hard Safety Kernel for topology v_next admission system.

Runs O(1) per file via prefix/glob matching. Returns kernel alerts independent
of profile selection. Per SCAFFOLD §1.6: must run regardless of any profile
match (Universal §15 G1 invariant).

Public:
    kernel_check(files, *, binding) -> list[IssueRecord]
    is_hard_stopped(files, binding) -> bool

Codex-importable: stdlib + PyYAML only.
Anti-sidecar: no phrase/task parameter. No profile selection.
"""
from __future__ import annotations

import fnmatch

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    IssueRecord,
    Severity,
)


def kernel_check(files: list[str], *, binding: BindingLayer) -> list[IssueRecord]:
    """
    Check each file in *files* against binding.coverage_map.hard_stop_paths.

    Returns one IssueRecord per matching file with severity=HARD_STOP.
    metadata includes 'matched_pattern' for diagnostic traceability.
    Runs independently of profile selection (Universal §15 G1).
    """
    alerts: list[IssueRecord] = []
    hard_stop_patterns = binding.coverage_map.hard_stop_paths

    for file_path in files:
        matched_pattern = _match_hard_stop(file_path, hard_stop_patterns)
        if matched_pattern is not None:
            alerts.append(IssueRecord(
                code="hard_stop_path",
                path=file_path,
                severity=Severity.HARD_STOP,
                message=(
                    f"File '{file_path}' matches hard_stop pattern '{matched_pattern}'. "
                    "This path is protected from modification via admission engine. "
                    "Hard stops run regardless of profile match."
                ),
                metadata={"matched_pattern": matched_pattern},
            ))

    return alerts


def is_hard_stopped(files: list[str], binding: BindingLayer) -> bool:
    """
    Return True if any file in *files* matches a hard_stop pattern.

    Convenience boolean for early-exit in admission_engine. Uses the same
    matching logic as kernel_check().
    """
    hard_stop_patterns = binding.coverage_map.hard_stop_paths
    return any(
        _match_hard_stop(f, hard_stop_patterns) is not None
        for f in files
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _match_hard_stop(
    file_path: str,
    patterns: tuple[str, ...],
) -> str | None:
    """
    Return the first matching hard_stop pattern for *file_path*, or None.

    Matching uses fnmatch glob semantics. Patterns ending with '/**' are
    treated as directory prefix matches: a file 'a/b/c.py' matches 'a/**'.
    """
    for pattern in patterns:
        if _fnmatch_path(file_path, pattern):
            return pattern
    return None


def _fnmatch_path(file_path: str, pattern: str) -> bool:
    """
    Match *file_path* against *pattern* using fnmatch glob semantics.

    Handles the common case of '/**' suffix patterns used for directory trees.
    Returns True/False.
    """
    # Direct fnmatch match
    if fnmatch.fnmatch(file_path, pattern):
        return True

    # Handle 'dir/**' patterns: match any file under that directory.
    # e.g. 'src/execution/**' should match 'src/execution/foo.py'
    if pattern.endswith("/**"):
        prefix = pattern[:-3]  # strip '/**'
        if file_path == prefix or file_path.startswith(prefix + "/"):
            return True

    return False
