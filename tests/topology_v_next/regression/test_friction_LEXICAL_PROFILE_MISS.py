# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.2, §5.2
"""
Friction-pattern regression: LEXICAL_PROFILE_MISS (intent_resolver component).

Closure mechanism (SCAFFOLD §5.2):
- Intent is the routing key; phrase never enters profile selection.
- Same files + same intent -> same resolved Intent regardless of phrase.

P1.1 testable component (partial per §8):
- intent_resolver.resolve_intent returns identical (intent, issues) for the
  same intent_value regardless of any surrounding hint or phrase.
- This test exercises the resolver in isolation; the full LEXICAL_PROFILE_MISS
  closure test (same files, two hint strings -> same profile_matched) requires
  the admission_engine which ships in P1.2.
"""
from __future__ import annotations

from pathlib import Path

from scripts.topology_v_next.dataclasses import Intent, Severity
from scripts.topology_v_next.intent_resolver import resolve_intent
from scripts.topology_v_next.profile_loader import load_binding_layer

WORKTREE_ROOT = Path(__file__).parent.parent.parent.parent
STUB_BINDING = WORKTREE_ROOT / "architecture" / "topology_v_next_binding.yaml"


class TestLexicalProfileMiss:
    """
    LEXICAL_PROFILE_MISS: two agents with the same intent but different phrasing
    must produce the same resolved Intent — phrase is never the routing key.

    P1.1 scope: verify resolve_intent is deterministic on intent_value alone,
    ignoring any hint/phrase context that the caller might hold.
    """

    def test_same_intent_string_always_resolves_same(self):
        """
        Core anti-LEXICAL_PROFILE_MISS invariant:
        resolve_intent("create_new", ...) always returns Intent.create_new,
        regardless of what phrase the caller used to arrive at that decision.
        """
        bl = load_binding_layer(STUB_BINDING)

        # Simulate two different agents with different task phrasings but same intent
        # (phrase is NEVER passed to resolve_intent — the resolver doesn't accept it)
        result_a_intent, result_a_issues = resolve_intent("create_new", binding=bl)
        result_b_intent, result_b_issues = resolve_intent("create_new", binding=bl)

        assert result_a_intent is result_b_intent
        assert result_a_issues == result_b_issues

    def test_same_intent_enum_always_resolves_same(self):
        bl = load_binding_layer(STUB_BINDING)

        result_a_intent, _ = resolve_intent(Intent.modify_existing, binding=bl)
        result_b_intent, _ = resolve_intent(Intent.modify_existing, binding=bl)

        assert result_a_intent is result_b_intent

    def test_intent_routing_independent_of_call_order(self):
        """
        Calling resolve_intent for different intents in different orders
        must produce independent, non-interfering results.
        """
        bl = load_binding_layer(STUB_BINDING)

        # Forward order
        a1, _ = resolve_intent("create_new", binding=bl)
        a2, _ = resolve_intent("refactor", binding=bl)

        # Reverse order
        b2, _ = resolve_intent("refactor", binding=bl)
        b1, _ = resolve_intent("create_new", binding=bl)

        assert a1 is b1  # create_new always maps to create_new
        assert a2 is b2  # refactor always maps to refactor
        assert a1 is not a2  # distinct intents remain distinct

    def test_zeus_extension_intent_same_regardless_of_context(self):
        """
        Zeus-namespaced intents must also be deterministic on value alone.
        """
        bl = load_binding_layer(STUB_BINDING)

        r1, issues1 = resolve_intent("zeus.topology_tooling", binding=bl)
        r2, issues2 = resolve_intent("zeus.topology_tooling", binding=bl)

        assert r1 is r2
        assert issues1 == issues2

    def test_phrase_is_never_a_parameter(self):
        """
        Structural guard: verify resolve_intent signature has no phrase-like
        parameters. This is also tested in test_intent_resolver.py but repeated
        here as a regression gate specific to LEXICAL_PROFILE_MISS closure.
        """
        import inspect
        sig = inspect.signature(resolve_intent)
        forbidden = {"task", "task_phrase", "phrase", "hint", "phrasing"}
        found = forbidden & set(sig.parameters.keys())
        assert found == set(), (
            f"resolve_intent must not accept phrase-like parameters: {found}. "
            "This would re-introduce LEXICAL_PROFILE_MISS at the resolver level."
        )

    def test_all_universal_intents_deterministic(self):
        """
        Every universal intent value resolves to exactly itself, idempotently.
        """
        bl = load_binding_layer(STUB_BINDING)
        universal_pairs = [
            ("plan_only", Intent.plan_only),
            ("create_new", Intent.create_new),
            ("modify_existing", Intent.modify_existing),
            ("refactor", Intent.refactor),
            ("audit", Intent.audit),
            ("hygiene", Intent.hygiene),
            ("hotfix", Intent.hotfix),
            ("rebase_keepup", Intent.rebase_keepup),
            ("other", Intent.other),
        ]
        for string_val, expected_enum in universal_pairs:
            resolved, issues = resolve_intent(string_val, binding=bl)
            assert resolved is expected_enum, (
                f"'{string_val}' should resolve to {expected_enum}, got {resolved}"
            )
            assert issues == [], f"Universal intent '{string_val}' should produce no issues"
