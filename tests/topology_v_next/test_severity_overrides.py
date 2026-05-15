# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.1, §8 P1.3
"""
Tests for scripts/topology_v_next/severity_overrides.py.

Covers per SCAFFOLD §2.1:
- override application (code matched → new severity)
- effective_severity ordering (HARD_STOP > SOFT_BLOCK > ADVISORY > ADMIT)
- no-mutation property (original IssueRecord instances unchanged)
- empty issue list edge cases
- no-op when overrides dict is empty
"""
from __future__ import annotations

import pytest

from scripts.topology_v_next.dataclasses import IssueRecord, Severity
from scripts.topology_v_next.severity_overrides import apply_overrides, effective_severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue(code: str, severity: Severity, path: str = "src/foo.py") -> IssueRecord:
    return IssueRecord(
        code=code,
        path=path,
        severity=severity,
        message=f"Test issue {code}",
        metadata={},
    )


# ---------------------------------------------------------------------------
# apply_overrides
# ---------------------------------------------------------------------------

class TestApplyOverrides:
    def test_empty_overrides_returns_original_list(self):
        issues = [_issue("coverage_gap", Severity.ADVISORY)]
        result = apply_overrides(issues, {})
        assert result is issues  # exact same object — no-op path

    def test_empty_issues_returns_empty(self):
        result = apply_overrides([], {"coverage_gap": Severity.SOFT_BLOCK})
        assert result == []

    def test_override_remaps_matching_code(self):
        issues = [_issue("coverage_gap", Severity.ADVISORY)]
        overrides = {"coverage_gap": Severity.SOFT_BLOCK}
        result = apply_overrides(issues, overrides)
        assert len(result) == 1
        assert result[0].severity == Severity.SOFT_BLOCK

    def test_override_preserves_other_fields(self):
        """Remapped issue must preserve code, path, message, metadata."""
        issue = _issue("coverage_gap", Severity.ADVISORY)
        overrides = {"coverage_gap": Severity.SOFT_BLOCK}
        result = apply_overrides([issue], overrides)
        remapped = result[0]
        assert remapped.code == issue.code
        assert remapped.path == issue.path
        assert remapped.message == issue.message
        assert remapped.metadata == issue.metadata

    def test_unmatched_code_passes_through_unchanged(self):
        issues = [_issue("composition_conflict", Severity.SOFT_BLOCK)]
        overrides = {"coverage_gap": Severity.ADVISORY}
        result = apply_overrides(issues, overrides)
        assert result[0] is issues[0]  # exact same object — not remapped

    def test_same_severity_does_not_create_new_instance(self):
        """If override maps to the same severity, original instance is reused."""
        issue = _issue("coverage_gap", Severity.ADVISORY)
        overrides = {"coverage_gap": Severity.ADVISORY}
        result = apply_overrides([issue], overrides)
        assert result[0] is issue

    def test_multiple_issues_some_overridden(self):
        issues = [
            _issue("coverage_gap", Severity.ADVISORY),
            _issue("composition_conflict", Severity.SOFT_BLOCK),
            _issue("closed_packet_authority", Severity.ADVISORY),
        ]
        overrides = {
            "coverage_gap": Severity.SOFT_BLOCK,
            "closed_packet_authority": Severity.SOFT_BLOCK,
        }
        result = apply_overrides(issues, overrides)
        assert len(result) == 3
        assert result[0].severity == Severity.SOFT_BLOCK
        assert result[1].severity == Severity.SOFT_BLOCK  # unchanged (was already SOFT_BLOCK)
        assert result[2].severity == Severity.SOFT_BLOCK

    def test_no_mutation_original_list_unchanged(self):
        """apply_overrides must not mutate the input list or its IssueRecords."""
        issues = [_issue("coverage_gap", Severity.ADVISORY)]
        original_severity = issues[0].severity
        _ = apply_overrides(issues, {"coverage_gap": Severity.HARD_STOP})
        assert issues[0].severity == original_severity  # frozen dataclass unchanged

    def test_promote_advisory_to_hard_stop(self):
        issues = [_issue("hard_stop_path", Severity.ADVISORY)]
        result = apply_overrides(issues, {"hard_stop_path": Severity.HARD_STOP})
        assert result[0].severity == Severity.HARD_STOP

    def test_demote_soft_block_to_advisory(self):
        issues = [_issue("companion_missing", Severity.SOFT_BLOCK)]
        result = apply_overrides(issues, {"companion_missing": Severity.ADVISORY})
        assert result[0].severity == Severity.ADVISORY


# ---------------------------------------------------------------------------
# effective_severity
# ---------------------------------------------------------------------------

class TestEffectiveSeverity:
    def test_empty_list_returns_admit(self):
        assert effective_severity([]) == Severity.ADMIT

    def test_single_advisory(self):
        assert effective_severity([_issue("x", Severity.ADVISORY)]) == Severity.ADVISORY

    def test_single_soft_block(self):
        assert effective_severity([_issue("x", Severity.SOFT_BLOCK)]) == Severity.SOFT_BLOCK

    def test_single_hard_stop(self):
        assert effective_severity([_issue("x", Severity.HARD_STOP)]) == Severity.HARD_STOP

    def test_hard_stop_dominates_advisory(self):
        issues = [
            _issue("a", Severity.ADVISORY),
            _issue("b", Severity.HARD_STOP),
            _issue("c", Severity.ADVISORY),
        ]
        assert effective_severity(issues) == Severity.HARD_STOP

    def test_soft_block_dominates_advisory(self):
        issues = [
            _issue("a", Severity.ADVISORY),
            _issue("b", Severity.SOFT_BLOCK),
        ]
        assert effective_severity(issues) == Severity.SOFT_BLOCK

    def test_hard_stop_dominates_soft_block(self):
        issues = [
            _issue("a", Severity.SOFT_BLOCK),
            _issue("b", Severity.HARD_STOP),
        ]
        assert effective_severity(issues) == Severity.HARD_STOP

    def test_ordering_admit_advisory_soft_hard(self):
        """Verify full ordering: ADMIT < ADVISORY < SOFT_BLOCK < HARD_STOP."""
        for lower, higher in [
            (Severity.ADMIT, Severity.ADVISORY),
            (Severity.ADVISORY, Severity.SOFT_BLOCK),
            (Severity.SOFT_BLOCK, Severity.HARD_STOP),
        ]:
            issues = [_issue("a", lower), _issue("b", higher)]
            assert effective_severity(issues) == higher

    def test_all_admit_returns_admit(self):
        issues = [_issue("a", Severity.ADMIT), _issue("b", Severity.ADMIT)]
        assert effective_severity(issues) == Severity.ADMIT


# ---------------------------------------------------------------------------
# Integration: apply_overrides then effective_severity
# ---------------------------------------------------------------------------

class TestApplyThenEffective:
    def test_override_then_effective(self):
        """Override advisory→soft_block, then effective_severity picks it up."""
        issues = [
            _issue("coverage_gap", Severity.ADVISORY),
            _issue("intent_unspecified", Severity.ADVISORY),
        ]
        overrides = {"coverage_gap": Severity.SOFT_BLOCK}
        remapped = apply_overrides(issues, overrides)
        assert effective_severity(remapped) == Severity.SOFT_BLOCK

    def test_no_override_effective_advisory(self):
        issues = [_issue("coverage_gap", Severity.ADVISORY)]
        remapped = apply_overrides(issues, {})
        assert effective_severity(remapped) == Severity.ADVISORY
