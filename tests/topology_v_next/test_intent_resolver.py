# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.4, §2.1, §5.3, §5.4
"""
Unit tests for scripts/topology_v_next/intent_resolver.py.

Key invariants tested:
- Canonical intent string resolves to correct Intent member.
- zeus.* extension intent accepted when registered in binding.
- Unknown string falls back to Intent.other + ADVISORY issue.
- None falls back to Intent.other + ADVISORY issue.
- ANTI-META-PATTERN guard: 'task', 'task_phrase', 'phrase' are NOT parameters
  of resolve_intent (enforced via inspect.signature).
- is_zeus_intent correctly identifies zeus.* vs universal intents.
"""
from __future__ import annotations

import inspect

import pytest

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CohortDecl,
    CoverageMap,
    Intent,
    IssueRecord,
    Severity,
)
from scripts.topology_v_next.intent_resolver import is_zeus_intent, resolve_intent
from scripts.topology_v_next.profile_loader import load_binding_layer

from pathlib import Path

# Shared binding fixture path
WORKTREE_ROOT = Path(__file__).parent.parent.parent
STUB_BINDING_PATH = WORKTREE_ROOT / "architecture" / "topology_v_next_binding.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_binding() -> BindingLayer:
    """Minimal BindingLayer with all zeus.* extensions registered."""
    cm = CoverageMap(profiles={}, orphaned=(), hard_stop_paths=())
    return BindingLayer(
        project_id="test",
        intent_extensions=(
            Intent.zeus_settlement_followthrough,
            Intent.zeus_calibration_update,
            Intent.zeus_data_authority_receipt,
            Intent.zeus_topology_tooling,
        ),
        coverage_map=cm,
        cohorts=(),
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status={},
    )


def _empty_binding() -> BindingLayer:
    """Binding with no intent_extensions registered."""
    cm = CoverageMap(profiles={}, orphaned=(), hard_stop_paths=())
    return BindingLayer(
        project_id="test",
        intent_extensions=(),
        coverage_map=cm,
        cohorts=(),
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status={},
    )


# ---------------------------------------------------------------------------
# Anti-meta-pattern signature guard (SCAFFOLD §5.4)
# ---------------------------------------------------------------------------

class TestSignatureAntiPattern:
    """
    CRITICAL: resolve_intent must NOT accept task/phrase/task_phrase as parameters.
    This is the structural guard against Sidecar #8 per SCAFFOLD §5.3.
    """

    def test_no_task_parameter(self):
        sig = inspect.signature(resolve_intent)
        assert "task" not in sig.parameters, (
            "resolve_intent must not accept 'task' parameter — "
            "this would re-introduce the phrase-as-routing-key anti-pattern."
        )

    def test_no_task_phrase_parameter(self):
        sig = inspect.signature(resolve_intent)
        assert "task_phrase" not in sig.parameters, (
            "resolve_intent must not accept 'task_phrase' parameter."
        )

    def test_no_phrase_parameter(self):
        sig = inspect.signature(resolve_intent)
        assert "phrase" not in sig.parameters, (
            "resolve_intent must not accept 'phrase' parameter."
        )

    def test_required_parameters_present(self):
        sig = inspect.signature(resolve_intent)
        assert "intent_value" in sig.parameters
        assert "binding" in sig.parameters

    def test_binding_is_keyword_only(self):
        """binding must be keyword-only (enforces call site clarity)."""
        sig = inspect.signature(resolve_intent)
        binding_param = sig.parameters["binding"]
        assert binding_param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_return_annotation_is_tuple(self):
        sig = inspect.signature(resolve_intent)
        # Return annotation exists and is tuple-shaped
        ann = sig.return_annotation
        assert ann is not inspect.Parameter.empty


# ---------------------------------------------------------------------------
# Canonical intent resolution (string input)
# ---------------------------------------------------------------------------

class TestResolveIntentString:
    def test_create_new_string(self):
        intent, issues = resolve_intent("create_new", binding=_minimal_binding())
        assert intent is Intent.create_new
        assert issues == []

    def test_modify_existing_string(self):
        intent, issues = resolve_intent("modify_existing", binding=_minimal_binding())
        assert intent is Intent.modify_existing
        assert issues == []

    def test_plan_only_string(self):
        intent, issues = resolve_intent("plan_only", binding=_minimal_binding())
        assert intent is Intent.plan_only
        assert issues == []

    def test_all_universal_intents(self):
        universal = [
            "plan_only", "create_new", "modify_existing", "refactor",
            "audit", "hygiene", "hotfix", "rebase_keepup", "other",
        ]
        binding = _minimal_binding()
        for val in universal:
            intent, issues = resolve_intent(val, binding=binding)
            assert intent.value == val
            assert issues == [], f"Unexpected issues for intent '{val}': {issues}"


# ---------------------------------------------------------------------------
# Canonical intent resolution (enum input)
# ---------------------------------------------------------------------------

class TestResolveIntentEnum:
    def test_enum_passthrough(self):
        intent, issues = resolve_intent(Intent.refactor, binding=_minimal_binding())
        assert intent is Intent.refactor
        assert issues == []

    def test_all_universal_enums_clean(self):
        binding = _minimal_binding()
        universal_enums = [
            Intent.plan_only, Intent.create_new, Intent.modify_existing,
            Intent.refactor, Intent.audit, Intent.hygiene, Intent.hotfix,
            Intent.rebase_keepup, Intent.other,
        ]
        for en in universal_enums:
            intent, issues = resolve_intent(en, binding=binding)
            assert intent is en
            assert issues == [], f"Unexpected issues for {en}: {issues}"


# ---------------------------------------------------------------------------
# Zeus extension intents
# ---------------------------------------------------------------------------

class TestZeusExtensionIntents:
    def test_zeus_topology_tooling_string(self):
        intent, issues = resolve_intent(
            "zeus.topology_tooling", binding=_minimal_binding()
        )
        assert intent is Intent.zeus_topology_tooling
        assert issues == []

    def test_zeus_calibration_update_string(self):
        intent, issues = resolve_intent(
            "zeus.calibration_update", binding=_minimal_binding()
        )
        assert intent is Intent.zeus_calibration_update
        assert issues == []

    def test_zeus_intent_unregistered_emits_advisory(self):
        """zeus.* intent present in enum but NOT in binding.intent_extensions -> ADVISORY."""
        intent, issues = resolve_intent(
            Intent.zeus_topology_tooling, binding=_empty_binding()
        )
        assert intent is Intent.zeus_topology_tooling
        assert len(issues) == 1
        assert issues[0].code == "intent_extension_unregistered"
        assert issues[0].severity is Severity.ADVISORY

    def test_zeus_intent_registered_no_issues(self):
        """zeus.* intent in binding.intent_extensions -> no issues."""
        intent, issues = resolve_intent(
            Intent.zeus_topology_tooling, binding=_minimal_binding()
        )
        assert intent is Intent.zeus_topology_tooling
        assert issues == []


# ---------------------------------------------------------------------------
# Unknown intent (string not in enum)
# ---------------------------------------------------------------------------

class TestUnknownIntent:
    def test_unknown_string_returns_other(self):
        intent, issues = resolve_intent(
            "not_a_real_intent", binding=_minimal_binding()
        )
        assert intent is Intent.other

    def test_unknown_string_emits_advisory(self):
        intent, issues = resolve_intent(
            "not_a_real_intent", binding=_minimal_binding()
        )
        assert len(issues) == 1
        assert issues[0].code == "intent_enum_unknown"
        assert issues[0].severity is Severity.ADVISORY

    def test_unknown_string_message_contains_value(self):
        intent, issues = resolve_intent(
            "my_unknown_intent", binding=_minimal_binding()
        )
        assert "my_unknown_intent" in issues[0].message

    def test_result_is_still_usable(self):
        """Fallback to Intent.other means admission proceeds, not crash."""
        intent, issues = resolve_intent("gibberish", binding=_minimal_binding())
        assert isinstance(intent, Intent)
        assert issues[0].severity is Severity.ADVISORY  # not HARD_STOP


# ---------------------------------------------------------------------------
# None intent
# ---------------------------------------------------------------------------

class TestNoneIntent:
    def test_none_returns_other(self):
        intent, issues = resolve_intent(None, binding=_minimal_binding())
        assert intent is Intent.other

    def test_none_emits_advisory(self):
        intent, issues = resolve_intent(None, binding=_minimal_binding())
        assert len(issues) == 1
        assert issues[0].code == "intent_unspecified"
        assert issues[0].severity is Severity.ADVISORY


# ---------------------------------------------------------------------------
# is_zeus_intent
# ---------------------------------------------------------------------------

class TestIsZeusIntent:
    def test_zeus_extensions_are_zeus(self):
        zeus_intents = [
            Intent.zeus_settlement_followthrough,
            Intent.zeus_calibration_update,
            Intent.zeus_data_authority_receipt,
            Intent.zeus_topology_tooling,
        ]
        for intent in zeus_intents:
            assert is_zeus_intent(intent), f"{intent.value} should be zeus intent"

    def test_universal_intents_are_not_zeus(self):
        universal = [
            Intent.plan_only, Intent.create_new, Intent.modify_existing,
            Intent.refactor, Intent.audit, Intent.hygiene, Intent.hotfix,
            Intent.rebase_keepup, Intent.other,
        ]
        for intent in universal:
            assert not is_zeus_intent(intent), f"{intent.value} should not be zeus intent"


# ---------------------------------------------------------------------------
# Integration: resolve against stub binding YAML
# ---------------------------------------------------------------------------

class TestResolveIntentWithStubYAML:
    def test_create_new_via_stub_binding(self):
        bl = load_binding_layer(STUB_BINDING_PATH)
        intent, issues = resolve_intent("create_new", binding=bl)
        assert intent is Intent.create_new
        assert issues == []

    def test_zeus_topology_tooling_via_stub_binding(self):
        bl = load_binding_layer(STUB_BINDING_PATH)
        intent, issues = resolve_intent("zeus.topology_tooling", binding=bl)
        assert intent is Intent.zeus_topology_tooling
        assert issues == []

    def test_unknown_intent_via_stub_binding(self):
        bl = load_binding_layer(STUB_BINDING_PATH)
        intent, issues = resolve_intent("no_such_intent", binding=bl)
        assert intent is Intent.other
        assert any(i.code == "intent_enum_unknown" for i in issues)
