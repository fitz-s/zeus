# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe5
"""
Probe 5 — INTENT_ENUM_TOO_NARROW: unknown intent emits intent_enum_unknown advisory.

Trigger: intent="frobnicate_thing" (not in canonical enum, not in zeus.* namespace).
Expected: v_next emits ADVISORY with intent_enum_unknown code; old side ignores unknown
intent string (no equivalent check).

Kill criterion: advisory list contains entry with code == "intent_enum_unknown".
If this assertion fails, INTENT_ENUM_TOO_NARROW structural fix is broken.
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit


FILES = ["scripts/topology_doctor.py"]
PAYLOAD = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe5IntentEnumUnknown:

    def test_unknown_intent_emits_advisory(self, monkeypatch):
        """intent_enum_unknown advisory is emitted for unrecognized intent string."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        result = maybe_shadow_compare(
            {**PAYLOAD},
            task="frobnicate the thing",
            files=FILES,
            intent="frobnicate_thing",
            v_next_shadow=True,
        )
        shadow = result["v_next_shadow"]

        assert shadow.get("error") is None, f"v_next raised: {shadow.get('error')}"

        # Kill criterion: advisory must contain intent_enum_unknown
        advisory_codes = [a["code"] for a in shadow["advisory"]]
        assert "intent_enum_unknown" in advisory_codes, (
            f"INTENT_ENUM_TOO_NARROW fix broken: intent_enum_unknown not in advisory. "
            f"Got advisory: {shadow['advisory']}"
        )

    def test_unknown_intent_format_output_direct(self):
        """format_output on decision from unknown intent has intent_enum_unknown advisory."""
        decision = admit(intent="frobnicate_thing", files=FILES)
        envelope = format_output(decision)

        advisory_codes = [a["code"] for a in envelope["advisory"]]
        assert "intent_enum_unknown" in advisory_codes, (
            f"intent_enum_unknown missing from advisory: {advisory_codes}"
        )

    def test_unknown_intent_intent_class_is_other(self):
        """Unknown intent resolves to intent_class='other' in v_next."""
        decision = admit(intent="frobnicate_thing", files=FILES)
        assert decision.intent_class.value == "other", (
            f"Expected 'other' for unknown intent, got {decision.intent_class.value!r}"
        )

    def test_envelope_ok_field_present_and_bool(self, monkeypatch):
        """ok field in envelope is always bool even for unknown intents."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        result = maybe_shadow_compare(
            {**PAYLOAD},
            task="frobnicate",
            files=FILES,
            intent="completely_made_up_intent_xyz",
            v_next_shadow=True,
        )
        assert isinstance(result["v_next_shadow"]["ok"], bool)
