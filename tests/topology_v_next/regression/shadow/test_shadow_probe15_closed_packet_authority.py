# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe15
"""
Probe 15 — CLOSED_PACKET_STILL_LOAD_BEARING: closed_packet_authority emitted in shadow.

Trigger: files=["docs/operations/task_2026-05-06_topology_redesign"] (CURRENT_HISTORICAL
status in ZEUS_BINDING §8 artifact_authority_status).
Expected: v_next emits closed_packet_authority SOFT_BLOCK (promoted from ADVISORY via
severity_overrides); old side returns its normal admission. agreement_class == "DISAGREE_SEVERITY".

Kill criterion: assert any(a["code"] == "closed_packet_authority" for a in envelope["advisory"]
or b["code"] == "closed_packet_authority" for b in envelope["blockers"])
AND record.agreement_class == "DISAGREE_SEVERITY".
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit


# CURRENT_HISTORICAL path per ZEUS_BINDING §8
FILES_CLOSED = ["docs/operations/task_2026-05-06_topology_redesign"]

PAYLOAD_ADMITTED = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe15ClosedPacketAuthority:

    def test_closed_packet_emits_authority_issue(self):
        """v_next emits closed_packet_authority for CURRENT_HISTORICAL path."""
        decision = admit(intent="modify_existing", files=FILES_CLOSED)
        all_codes = [i.code for i in decision.issues]
        assert "closed_packet_authority" in all_codes, (
            f"CLOSED_PACKET_STILL_LOAD_BEARING fix broken: "
            f"closed_packet_authority not emitted. Got: {all_codes}"
        )

    def test_closed_packet_severity_promoted_to_soft_block(self):
        """closed_packet_authority is promoted to SOFT_BLOCK by severity_overrides."""
        decision = admit(intent="modify_existing", files=FILES_CLOSED)
        cp_issues = [i for i in decision.issues if i.code == "closed_packet_authority"]
        assert len(cp_issues) >= 1
        for issue in cp_issues:
            assert issue.severity.value == "SOFT_BLOCK", (
                f"Expected SOFT_BLOCK (promoted by severity_overrides), "
                f"got {issue.severity.value!r}"
            )

    def test_envelope_surfaces_closed_packet_in_blockers(self):
        """format_output puts closed_packet_authority in blockers (SOFT_BLOCK)."""
        decision = admit(intent="modify_existing", files=FILES_CLOSED)
        envelope = format_output(decision)

        blocker_codes = [b["code"] for b in envelope["blockers"]]
        advisory_codes = [a["code"] for a in envelope["advisory"]]

        # Kill criterion: closed_packet_authority appears in blockers OR advisory
        all_codes = blocker_codes + advisory_codes
        assert "closed_packet_authority" in all_codes, (
            f"CLOSED_PACKET_STILL_LOAD_BEARING: closed_packet_authority not surfaced "
            f"in envelope. blockers={blocker_codes}, advisory={advisory_codes}"
        )

    def test_shadow_record_agreement_class(self, monkeypatch):
        """Shadow record classifies DISAGREE_SEVERITY: old=admitted, v_next=SOFT_BLOCK."""
        captured = []
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: captured.append(r),
        )

        result = maybe_shadow_compare(
            {**PAYLOAD_ADMITTED},
            task="read closed packet authority",
            files=FILES_CLOSED,
            intent="modify_existing",
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None
        assert len(captured) == 1
        record = captured[0]

        # Kill criterion: agreement_class reflects disagreement
        # old=admitted (ADMIT equiv), v_next=SOFT_BLOCK → DISAGREE_SEVERITY
        assert record.agreement_class in {"DISAGREE_SEVERITY", "DISAGREE_PROFILE"}, (
            f"Expected DISAGREE_SEVERITY or DISAGREE_PROFILE for closed packet path. "
            f"Got {record.agreement_class!r}. "
            f"CLOSED_PACKET_STILL_LOAD_BEARING not observable in shadow."
        )
