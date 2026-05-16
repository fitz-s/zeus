# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe8
"""
Probe 8 — MISSING_COMPANION shadow event captured (P2 integration).

Trigger: files=["src/data/vendor_response_x.py"] or ["src/data/replay_x.py"]
(profile with companion_required that is NOT in the submitted files).
Expected: v_next emits missing_companion ADVISORY; record's missing_companion field
is non-empty; agreement_class == "DISAGREE_COMPANION" (old side has no companion check).

Kill criterion: assert record.missing_companion is non-empty
AND assert record.agreement_class == "DISAGREE_COMPANION".
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import Severity


# src/data/replay_x.py matches modify_data_replay_surface profile
# which requires companion: docs/reference/zeus_data_and_replay_reference.md
FILES = ["src/data/replay_x.py"]
PAYLOAD_ADMITTED = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe8MissingCompanionCapture:

    def test_vnext_emits_missing_companion_issue(self):
        """v_next emits missing_companion for replay_x.py without companion doc."""
        decision = admit(intent="modify_existing", files=FILES)
        issue_codes = [i.code for i in decision.issues]
        assert "missing_companion" in issue_codes, (
            f"Expected missing_companion issue from v_next for {FILES}. "
            f"Got issues: {issue_codes}"
        )

    def test_missing_companion_in_envelope_advisory(self):
        """format_output surfaces missing_companion in advisory list."""
        decision = admit(intent="modify_existing", files=FILES)
        envelope = format_output(decision)

        advisory_codes = [a["code"] for a in envelope["advisory"]]
        assert "missing_companion" in advisory_codes, (
            f"missing_companion not in advisory: {advisory_codes}"
        )

    def test_divergence_record_missing_companion_field(self, monkeypatch):
        """DivergenceRecord.missing_companion is non-empty for replay_x.py."""
        captured = []
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: captured.append(r),
        )

        result = maybe_shadow_compare(
            {**PAYLOAD_ADMITTED},
            task="modify data replay",
            files=FILES,
            intent="modify_existing",
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None

        assert len(captured) == 1
        record = captured[0]

        # Kill criterion part 1: missing_companion field is non-empty
        assert len(record.missing_companion) >= 1, (
            f"Expected non-empty missing_companion in DivergenceRecord, got: {record.missing_companion}"
        )

    def test_divergence_record_agreement_class_disagree_companion(self, monkeypatch):
        """agreement_class == DISAGREE_COMPANION when old=admitted and v_next has missing_companion."""
        captured = []
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: captured.append(r),
        )

        result = maybe_shadow_compare(
            {**PAYLOAD_ADMITTED},
            task="modify data replay",
            files=FILES,
            intent="modify_existing",
            v_next_shadow=True,
        )

        assert len(captured) == 1
        record = captured[0]

        # Kill criterion part 2: agreement_class == DISAGREE_COMPANION
        # (old=admitted, v_next=missing_companion advisory with ADMIT old_status)
        assert record.agreement_class == "DISAGREE_COMPANION", (
            f"Expected DISAGREE_COMPANION (P2 drift old side missed), "
            f"got {record.agreement_class!r}. "
            f"old_admit_status={record.old_admit_status!r}, "
            f"missing_companion={record.missing_companion}"
        )
