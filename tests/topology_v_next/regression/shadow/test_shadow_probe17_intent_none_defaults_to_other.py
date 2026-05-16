# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe17
"""
Probe 17 — intent=None defaults to Intent.other + emits intent_unspecified ADVISORY.

Trigger: invoke maybe_shadow_compare with intent=None (caller did not supply typed intent).
Expected: v_next resolves intent=None to Intent.other via intent_resolver;
emits ADVISORY intent_unspecified; record.intent_supplied=None, record.intent_typed="other".

Kill criteria (all must pass):
1. assert record.intent_supplied is None
2. assert record.intent_typed == "other"
3. assert any(a["code"] == "intent_unspecified" for a in envelope["advisory"])
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


class TestProbe17IntentNoneDefaultsToOther:

    def test_intent_none_resolves_to_other(self):
        """v_next resolves intent=None to Intent.other."""
        decision = admit(intent=None, files=FILES)
        assert decision.intent_class.value == "other", (
            f"Expected intent_class='other' for intent=None, "
            f"got {decision.intent_class.value!r}"
        )

    def test_intent_none_emits_intent_unspecified_advisory(self):
        """v_next emits intent_unspecified ADVISORY when intent=None."""
        decision = admit(intent=None, files=FILES)
        advisory_codes = [i.code for i in decision.issues]
        assert "intent_unspecified" in advisory_codes, (
            f"Kill criterion 3 failed: intent_unspecified advisory not emitted. "
            f"Got issues: {advisory_codes}"
        )

    def test_divergence_record_intent_supplied_is_none(self, monkeypatch):
        """DivergenceRecord.intent_supplied is None when caller passes intent=None."""
        captured = []
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: captured.append(r),
        )

        result = maybe_shadow_compare(
            {**PAYLOAD},
            task="some navigation task",
            files=FILES,
            intent=None,
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None, (
            f"intent=None caused v_next to raise: {shadow.get('error')}"
        )

        assert len(captured) == 1
        record = captured[0]

        # Kill criterion 1: intent_supplied is None
        assert record.intent_supplied is None, (
            f"Kill criterion 1 failed: expected intent_supplied=None, "
            f"got {record.intent_supplied!r}"
        )

        # Kill criterion 2: intent_typed == "other"
        assert record.intent_typed == "other", (
            f"Kill criterion 2 failed: expected intent_typed='other', "
            f"got {record.intent_typed!r}"
        )

    def test_envelope_advisory_contains_intent_unspecified(self, monkeypatch):
        """envelope['advisory'] contains intent_unspecified for intent=None."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: None,
        )

        result = maybe_shadow_compare(
            {**PAYLOAD},
            task="some navigation task",
            files=FILES,
            intent=None,
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None

        # Kill criterion 3: intent_unspecified in advisory
        advisory_codes = [a["code"] for a in shadow["advisory"]]
        assert "intent_unspecified" in advisory_codes, (
            f"Kill criterion 3 failed: intent_unspecified not in advisory. "
            f"Got: {advisory_codes}"
        )

    def test_intent_none_does_not_raise(self, monkeypatch):
        """intent=None is handled gracefully — no exception in maybe_shadow_compare."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: None,
        )

        # This must not raise
        result = maybe_shadow_compare(
            {**PAYLOAD},
            task="fallback navigation",
            files=FILES,
            intent=None,
            v_next_shadow=True,
        )
        assert "v_next_shadow" in result
        assert result["v_next_shadow"].get("error") is None
