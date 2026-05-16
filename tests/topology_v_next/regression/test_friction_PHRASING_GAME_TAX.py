# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.2
"""
Friction regression: PHRASING_GAME_TAX (P1.2 engine-internal variant).

SCAFFOLD §5.2: "Traceable closed (P1) — same intent + files always returns
same AdmissionDecision regardless of phrase. Deterministic on (intent, files)."

NOTE: The cross-module variant (§8 P1.3 deliverable) promotes this to a
full admit() round-trip. This P1.2 variant asserts engine-internal
determinism: composition_rules + admission_engine alone, identical results
for 3 different hint strings with same intent + files.
"""
from __future__ import annotations

import pytest

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
)


def _make_binding() -> BindingLayer:
    from scripts.topology_v_next.dataclasses import BindingLayer, CoverageMap
    cm = CoverageMap(
        profiles={
            "agent_runtime": (
                "scripts/topology_doctor.py",
                "architecture/test_topology.yaml",
            ),
            "test_suite": ("tests/test_*.py",),
        },
        orphaned=("tmp/**",),
        hard_stop_paths=("src/execution/**",),
    )
    return BindingLayer(
        project_id="zeus",
        intent_extensions=(Intent.zeus_topology_tooling,),
        coverage_map=cm,
        cohorts=(),
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status={},
    )


FILES = ["scripts/topology_doctor.py"]
INTENT = Intent.modify_existing

HINT_VARIANTS = [
    "fix the topology tool for better routing",
    "update topology_doctor to handle new profiles",
    "quick patch to topology script",
]


class TestPhrasingGameTaxClosure:
    def test_same_profile_matched_for_all_hints(self):
        """
        PHRASING_GAME_TAX CLOSED: profile_matched is identical for all hint variants.
        Hint cannot influence routing.
        """
        binding = _make_binding()
        results = [
            admit(intent=INTENT, files=FILES, hint=hint, binding=binding)
            for hint in HINT_VARIANTS
        ]
        profiles = {r.profile_matched for r in results}
        assert len(profiles) == 1, (
            f"profile_matched varied with hint: {profiles}. "
            "Hint must NOT influence profile selection."
        )

    def test_same_ok_for_all_hints(self):
        """ok must be identical regardless of hint."""
        binding = _make_binding()
        results = [
            admit(intent=INTENT, files=FILES, hint=hint, binding=binding)
            for hint in HINT_VARIANTS
        ]
        ok_values = {r.ok for r in results}
        assert len(ok_values) == 1

    def test_same_severity_for_all_hints(self):
        """severity must be identical regardless of hint."""
        binding = _make_binding()
        results = [
            admit(intent=INTENT, files=FILES, hint=hint, binding=binding)
            for hint in HINT_VARIANTS
        ]
        severities = {r.severity for r in results}
        assert len(severities) == 1

    def test_same_issue_codes_for_all_hints(self):
        """Issue codes must be identical regardless of hint."""
        binding = _make_binding()
        results = [
            admit(intent=INTENT, files=FILES, hint=hint, binding=binding)
            for hint in HINT_VARIANTS
        ]
        issue_code_sets = [frozenset(i.code for i in r.issues) for r in results]
        assert len(set(issue_code_sets)) == 1, (
            f"Issue codes varied with hint: {issue_code_sets}"
        )

    def test_friction_budget_unchanged_by_hint(self):
        """
        friction_budget_used must be 1 for all hint variants when no
        friction_state is supplied (P1 default — no SLICING_PRESSURE detection).
        """
        binding = _make_binding()
        results = [
            admit(intent=INTENT, files=FILES, hint=hint, binding=binding)
            for hint in HINT_VARIANTS
        ]
        budgets = {r.friction_budget_used for r in results}
        assert budgets == {1}

    def test_closest_rejected_profile_only_field_that_may_vary(self):
        """
        closest_rejected_profile is the ONLY field allowed to vary with hint
        (it's diagnostic-only, never gates routing).
        Profile_matched, ok, severity, issues must all be constant.
        """
        binding = _make_binding()
        r1 = admit(intent=INTENT, files=FILES, hint=HINT_VARIANTS[0], binding=binding)
        r2 = admit(intent=INTENT, files=FILES, hint=HINT_VARIANTS[1], binding=binding)

        assert r1.profile_matched == r2.profile_matched
        assert r1.ok == r2.ok
        assert r1.severity == r2.severity
        # closest_rejected_profile MAY differ — that's acceptable
        # (it's the one field hint legitimately influences)

    def test_empty_hint_produces_same_result_as_nonempty(self):
        """Empty hint string must produce the same routing as any hint."""
        binding = _make_binding()
        r_empty = admit(intent=INTENT, files=FILES, hint="", binding=binding)
        r_full = admit(intent=INTENT, files=FILES, hint="detailed description", binding=binding)

        assert r_empty.profile_matched == r_full.profile_matched
        assert r_empty.ok == r_full.ok
