# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe2
"""
Probe 2 — UNION_SCOPE_EXPANSION: v_next uses cohort declaration to admit via cohort.

Trigger: files that span two profiles but are declared as a cohort in the binding.
v_next should admit via cohort (or produce ADVISORY at worst); old side produces
advisory_only or scope_expansion_required when it doesn't understand cohorts.

Kill criterion: v_next does NOT produce an error; envelope fields are populated.
If v_next emits SOFT_BLOCK for this case, record agreement_class is computed correctly.
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import Severity


# Both files are in the calibration profile — cohort-adjacent test
FILES_COHORT = ["src/calibration/platt.py", "tests/test_calibration_platt.py"]

PAYLOAD_SCOPE_EXPANSION = {
    "ok": False,
    "admission": {"status": "scope_expansion_required"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe2UnionScopeExpansion:

    def test_vnext_envelope_populated_on_cohort_files(self, monkeypatch):
        """v_next returns a well-formed envelope for cohort file set."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        payload = maybe_shadow_compare(
            PAYLOAD_SCOPE_EXPANSION,
            task="update calibration platt",
            files=FILES_COHORT,
            intent="modify_existing",
            v_next_shadow=True,
        )
        shadow = payload["v_next_shadow"]

        # No error in shadow
        assert shadow.get("error") is None, f"v_next raised unexpectedly: {shadow.get('error')}"

        # Envelope has all mandatory fields
        assert "ok" in shadow
        assert "decision" in shadow
        assert isinstance(shadow["advisory"], list)
        assert isinstance(shadow["blockers"], list)

    def test_old_payload_fields_unchanged(self, monkeypatch):
        """Original payload fields are not mutated by shadow compare."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        original_ok = PAYLOAD_SCOPE_EXPANSION["ok"]
        payload = maybe_shadow_compare(
            {**PAYLOAD_SCOPE_EXPANSION},
            task="update calibration platt",
            files=FILES_COHORT,
            intent="modify_existing",
            v_next_shadow=True,
        )

        assert payload["ok"] == original_ok
        assert payload["admission"]["status"] == "scope_expansion_required"

    def test_format_output_decision_is_severity_value(self, monkeypatch):
        """format_output produces decision matching severity.value string."""
        d = admit(intent="modify_existing", files=FILES_COHORT)
        envelope = format_output(d)

        assert envelope["decision"] in {"ADMIT", "ADVISORY", "SOFT_BLOCK", "HARD_STOP"}
        assert isinstance(envelope["ok"], bool)

    def test_advisory_and_blockers_are_lists(self, monkeypatch):
        """advisory and blockers are always lists (never None)."""
        d = admit(intent="modify_existing", files=FILES_COHORT)
        envelope = format_output(d)

        assert isinstance(envelope["advisory"], list)
        assert isinstance(envelope["blockers"], list)
