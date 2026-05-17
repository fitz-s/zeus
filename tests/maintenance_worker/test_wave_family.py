# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   maintenance_worker/rules/wave_family.py
#   docs/authority/ARCHIVAL_RULES.md
#   §"Special Case: Wave Packets"
"""
Tests for maintenance_worker.rules.wave_family.

Covers:
  - 3-packet wave family with mixed verdicts → all 3 stay (atomic group)
  - 3-packet wave family where all pass → family archivable
  - Non-wave candidates excluded from group_by_wave_family output
  - Multiple families grouped independently
  - Empty family → wave_family_exemption_atomic returns False
  - wave_family_exemption_atomic semantics (any-fail → stay)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from maintenance_worker.rules.wave_family import (
    group_by_wave_family,
    wave_family_exemption_atomic,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _paths(*names: str) -> list[Path]:
    """Create a list of Path objects from name strings (no real filesystem)."""
    return [Path(n) for n in names]


# ---------------------------------------------------------------------------
# Tests: group_by_wave_family
# ---------------------------------------------------------------------------

class TestGroupByWaveFamily:
    def test_three_wave_packets_grouped_under_one_key(self) -> None:
        candidates = _paths(
            "task_2026-05-15_authority_rehydration_wave1",
            "task_2026-05-15_authority_rehydration_wave2",
            "task_2026-05-15_authority_rehydration_wave3",
        )
        result = group_by_wave_family(candidates)
        assert len(result) == 1
        key = "2026-05-15_authority_rehydration"
        assert key in result
        assert len(result[key]) == 3

    def test_non_wave_candidates_excluded(self) -> None:
        candidates = _paths(
            "task_2026-05-15_authority_rehydration_wave1",
            "task_2026-05-10_some_regular_task",       # no _waveN suffix
            "task_2026-05-12_another_task",            # no _waveN suffix
            "not_a_task_at_all",
        )
        result = group_by_wave_family(candidates)
        # Only the wave packet is grouped
        assert len(result) == 1
        assert "2026-05-15_authority_rehydration" in result
        assert len(result["2026-05-15_authority_rehydration"]) == 1

    def test_two_independent_families_grouped_separately(self) -> None:
        candidates = _paths(
            "task_2026-05-15_authority_rehydration_wave1",
            "task_2026-05-15_authority_rehydration_wave2",
            "task_2026-05-14_topology_redesign_wave1",
            "task_2026-05-14_topology_redesign_wave2",
        )
        result = group_by_wave_family(candidates)
        assert len(result) == 2
        assert "2026-05-15_authority_rehydration" in result
        assert "2026-05-14_topology_redesign" in result
        assert len(result["2026-05-15_authority_rehydration"]) == 2
        assert len(result["2026-05-14_topology_redesign"]) == 2

    def test_empty_candidates_returns_empty_dict(self) -> None:
        result = group_by_wave_family([])
        assert result == {}

    def test_slug_with_underscores_matched_correctly(self) -> None:
        # slug itself contains underscores — must not break the regex
        candidates = _paths(
            "task_2026-05-16_doc_alignment_plan_wave1",
            "task_2026-05-16_doc_alignment_plan_wave2",
        )
        result = group_by_wave_family(candidates)
        assert len(result) == 1
        # Family key should preserve the full slug (non-greedy match keeps all underscores)
        key = list(result.keys())[0]
        assert key.startswith("2026-05-16_")
        assert key.endswith("_plan")
        assert len(result[key]) == 2

    def test_file_paths_with_extensions_use_stem(self) -> None:
        # Some candidates may be files rather than directories
        candidates = [
            Path("task_2026-05-15_foo_wave1.md"),
            Path("task_2026-05-15_foo_wave2.md"),
        ]
        result = group_by_wave_family(candidates)
        assert len(result) == 1
        assert len(list(result.values())[0]) == 2


# ---------------------------------------------------------------------------
# Tests: wave_family_exemption_atomic
# ---------------------------------------------------------------------------

class TestWaveFamilyExemptionAtomic:
    def test_mixed_verdicts_all_three_stay(self) -> None:
        """
        3-packet family: wave1 passes, wave2 fails, wave3 passes.
        Any failure → whole family exempted → returns True (all stay).
        """
        family = _paths(
            "task_2026-05-15_authority_rehydration_wave1",
            "task_2026-05-15_authority_rehydration_wave2",
            "task_2026-05-15_authority_rehydration_wave3",
        )
        # wave2 fails check (returns False for wave2)
        failing_name = "task_2026-05-15_authority_rehydration_wave2"

        def check_one(p: Path) -> bool:
            return p.name != failing_name

        result = wave_family_exemption_atomic(family, check_one)
        assert result is True  # family stays

    def test_all_pass_family_archivable(self) -> None:
        """All 3 pass → family may be archived → returns False."""
        family = _paths(
            "task_2026-05-14_old_topology_wave1",
            "task_2026-05-14_old_topology_wave2",
            "task_2026-05-14_old_topology_wave3",
        )

        def check_one(p: Path) -> bool:
            return True  # all pass

        result = wave_family_exemption_atomic(family, check_one)
        assert result is False  # family archivable

    def test_all_fail_returns_true(self) -> None:
        """All fail → exempted → True."""
        family = _paths(
            "task_2026-05-01_big_refactor_wave1",
            "task_2026-05-01_big_refactor_wave2",
        )

        def check_one(p: Path) -> bool:
            return False  # all fail

        result = wave_family_exemption_atomic(family, check_one)
        assert result is True

    def test_single_member_fail_exempts_family(self) -> None:
        family = _paths("task_2026-05-10_calibration_wave1")

        def check_one(p: Path) -> bool:
            return False

        assert wave_family_exemption_atomic(family, check_one) is True

    def test_single_member_pass_allows_archive(self) -> None:
        family = _paths("task_2026-05-10_calibration_wave1")

        def check_one(p: Path) -> bool:
            return True

        assert wave_family_exemption_atomic(family, check_one) is False

    def test_empty_family_returns_false(self) -> None:
        """Empty family → nothing to block → archivable."""
        assert wave_family_exemption_atomic([], lambda p: True) is False
        assert wave_family_exemption_atomic([], lambda p: False) is False

    def test_non_wave_packets_unaffected_by_wave_logic(self) -> None:
        """
        Non-wave packets should not be passed to wave_family_exemption_atomic.
        group_by_wave_family excludes them. Verify that a family containing
        only properly-grouped wave paths behaves independently.
        """
        # Simulate two families: only family A is passed here
        family_a = _paths(
            "task_2026-05-15_authority_rehydration_wave1",
            "task_2026-05-15_authority_rehydration_wave2",
        )
        # family_b stays in its own call — not mixed in here
        family_b = _paths(
            "task_2026-05-14_topology_redesign_wave1",
        )

        # family_a: wave2 fails
        def check_a(p: Path) -> bool:
            return "wave2" not in p.name

        # family_b: all pass
        def check_b(p: Path) -> bool:
            return True

        assert wave_family_exemption_atomic(family_a, check_a) is True   # family_a stays
        assert wave_family_exemption_atomic(family_b, check_b) is False  # family_b archives
