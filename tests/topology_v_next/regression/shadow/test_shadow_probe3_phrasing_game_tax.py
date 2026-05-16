# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe3
"""
Probe 3 — PHRASING_GAME_TAX: hash-hint ensures phrase-independent routing and diagnostics.

Trigger: 3 calls with same files + intent but different task phrase.
Kill criterion (two assertions, both must pass):
1. All 3 records produce the same (profile_resolved_new, new_admit_severity).
2. All 3 envelopes produce the same closest_rejected_profile (hash hint prevents
   phrase-sensitive diagnostic output).

If assertion 2 fails, the raw task string is leaking through hint instead of the hash.
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit


FILES = ["scripts/topology_doctor.py"]
INTENT = "create_new"
PHRASES = ["add a module", "add module logic", "create the module"]

PAYLOAD = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe3PhrasingGameTax:

    def test_routing_phrase_independent(self, monkeypatch):
        """3 phrase-varying calls produce identical (profile_matched, severity)."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        results = []
        for phrase in PHRASES:
            result = maybe_shadow_compare(
                {**PAYLOAD},
                task=phrase,
                files=FILES,
                intent=INTENT,
                v_next_shadow=True,
            )
            results.append(result["v_next_shadow"])

        routing_signatures = {
            (r["profile_matched"], r["decision"]) for r in results
        }
        # Kill criterion 1: routing must be phrase-independent
        assert len(routing_signatures) == 1, (
            f"PHRASING_GAME_TAX: phrase-varying calls produced different routing: {routing_signatures}"
        )

    def test_diagnostic_phrase_independent(self, monkeypatch):
        """closest_rejected_profile is identical across phrase-varying calls (hash-hint guard)."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        results = []
        for phrase in PHRASES:
            result = maybe_shadow_compare(
                {**PAYLOAD},
                task=phrase,
                files=FILES,
                intent=INTENT,
                v_next_shadow=True,
            )
            results.append(result["v_next_shadow"])

        closest_profiles = {r["closest_rejected_profile"] for r in results}
        # Kill criterion 2: diagnostic output must be phrase-independent
        assert len(closest_profiles) == 1, (
            f"PHRASING_GAME_TAX: closest_rejected_profile varies across phrase variants. "
            f"Raw task string may be leaking through hint parameter: {closest_profiles}"
        )

    def test_shim_has_no_phrase_parameter(self):
        """Introspect maybe_shadow_compare signature — no phrase/task_phrase/wording param."""
        import inspect
        sig = inspect.signature(maybe_shadow_compare)
        banned = {"phrase", "task_phrase", "wording", "hint_text", "description"}
        param_names = set(sig.parameters.keys())
        violations = param_names & banned
        assert not violations, (
            f"Anti-PHRASING_GAME_TAX guard violated: shim has banned parameters {violations}"
        )

    def test_format_output_has_no_phrase_parameter(self):
        """Introspect format_output signature — no phrase/task_phrase/wording param."""
        import inspect
        sig = inspect.signature(format_output)
        banned = {"phrase", "task_phrase", "wording", "hint_text"}
        param_names = set(sig.parameters.keys())
        violations = param_names & banned
        assert not violations, (
            f"format_output has banned parameters {violations}"
        )
