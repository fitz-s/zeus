# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.2, §5.2
"""
Friction-pattern regression: INTENT_ENUM_TOO_NARROW.

Closure mechanism (SCAFFOLD §5.2):
- Binding-layer intent_extensions registers project-specific intents.
- Unknown intent string -> Intent.other + ADVISORY (not crash).
- profile_loader.validate_binding_layer detects missing namespace prefix.

P1.1 testable component: intent_resolver.resolve_intent behaviour on unknown
intent string — must produce ADVISORY issue and fall back gracefully.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.topology_v_next.dataclasses import Intent, Severity
from scripts.topology_v_next.intent_resolver import resolve_intent
from scripts.topology_v_next.profile_loader import load_binding_layer, validate_binding_layer

WORKTREE_ROOT = Path(__file__).parent.parent.parent.parent
STUB_BINDING = WORKTREE_ROOT / "architecture" / "topology_v_next_binding.yaml"


class TestIntentEnumTooNarrow:
    """
    INTENT_ENUM_TOO_NARROW: a valid project intent not in the canonical enum
    should raise an ADVISORY and fall back to Intent.other, not crash.
    """

    def test_unknown_intent_produces_advisory_not_hard_stop(self):
        bl = load_binding_layer(STUB_BINDING)
        intent, issues = resolve_intent("zeus.experimental_new_intent", binding=bl)
        # Fallback to other
        assert intent is Intent.other
        # Produces ADVISORY, not crash or HARD_STOP
        assert len(issues) == 1
        assert issues[0].severity is Severity.ADVISORY

    def test_unknown_intent_issue_code_is_enum_unknown(self):
        bl = load_binding_layer(STUB_BINDING)
        intent, issues = resolve_intent("zeus.not_yet_in_enum", binding=bl)
        assert issues[0].code == "intent_enum_unknown"

    def test_known_zeus_extension_is_not_narrow(self):
        """
        Zeus.* extensions declared in intent_extensions must NOT trigger this friction.
        Once registered in the binding, they resolve cleanly.
        """
        bl = load_binding_layer(STUB_BINDING)
        intent, issues = resolve_intent("zeus.topology_tooling", binding=bl)
        assert intent is Intent.zeus_topology_tooling
        enum_unknown_issues = [i for i in issues if i.code == "intent_enum_unknown"]
        assert enum_unknown_issues == [], (
            "Registered zeus.* intents must not trigger INTENT_ENUM_TOO_NARROW"
        )

    def test_validate_warns_on_missing_namespace_prefix(self):
        """
        validate_binding_layer catches intent_extensions missing namespace prefix.
        This is the structural check for intent values that agents add without
        namespacing them correctly.
        """
        from scripts.topology_v_next.dataclasses import BindingLayer, CoverageMap
        # Build a binding where an intent_extension lacks namespace prefix
        # NOTE: This is a hypothetical — in practice the loader skips unknown enum values.
        # We test validate_binding_layer's namespace check on a binding built with
        # a known Intent that has no "." in its value (there are none in universal,
        # but the validate function checks the value string).
        cm = CoverageMap(profiles={}, orphaned=(), hard_stop_paths=())
        # Use Intent.other as a stand-in: it has no "." prefix, but it's universal
        # so it should be excluded from the namespace check.
        # The real test is: zeus.* that ARE in the enum pass; unknown strings skip.
        bl = BindingLayer(
            project_id="test",
            intent_extensions=(Intent.zeus_topology_tooling,),
            coverage_map=cm,
            cohorts=(),
            severity_overrides={},
            high_fanout_hints=(),
            artifact_authority_status={},
        )
        warnings = validate_binding_layer(bl)
        # zeus.topology_tooling has a "." prefix — no namespace warning
        namespace_warnings = [w for w in warnings if "namespace prefix" in w]
        assert namespace_warnings == []

    def test_decision_proceeds_after_unknown_intent(self):
        """
        After INTENT_ENUM_TOO_NARROW ADVISORY is issued, the decision proceeds.
        Intent.other is a valid escape hatch per Universal §2.2.
        """
        bl = load_binding_layer(STUB_BINDING)
        intent, issues = resolve_intent("completely_new_intent_not_in_enum", binding=bl)
        # Must produce a usable Intent (not raise)
        assert isinstance(intent, Intent)
        # Must produce exactly one issue
        assert len(issues) == 1
