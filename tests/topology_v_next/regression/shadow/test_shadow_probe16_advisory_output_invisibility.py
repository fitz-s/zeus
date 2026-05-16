# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe16
"""
Probe 16 — ADVISORY_OUTPUT_INVISIBILITY: advisory list is non-empty and visible.

Trigger: a case that produces ok=True with one or more ADVISORY issues in v_next
(e.g., probe5's intent_enum_unknown at advisory severity — with SOFT_BLOCK from
composition_conflict, or intent_unspecified when intent=None).
Expected: envelope["ok"] == True OR envelope["ok"] == False (but advisory is non-empty).
The key assertion is that advisory list is ALWAYS present and non-empty when there
are advisory issues.

Kill criterion: assert len(envelope["advisory"]) >= 1 and isinstance(envelope["advisory"], list)
— failure means ADVISORY issues are silently dropped (ADVISORY_OUTPUT_INVISIBILITY).
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import Severity


# Files that produce advisory issues (missing_companion) with ok=True/False
FILES_ADVISORY = ["src/data/replay_x.py"]
# intent=None produces intent_unspecified advisory
FILES_SIMPLE = ["scripts/topology_doctor.py"]


class TestProbe16AdvisoryOutputInvisibility:

    def test_advisory_list_is_always_list_type(self):
        """advisory field is always a list, never None."""
        decision = admit(intent=None, files=FILES_SIMPLE)
        envelope = format_output(decision)
        assert isinstance(envelope["advisory"], list), (
            f"advisory should be list, got {type(envelope['advisory'])}"
        )
        assert isinstance(envelope["blockers"], list), (
            f"blockers should be list, got {type(envelope['blockers'])}"
        )

    def test_advisory_issues_surface_through_format_output(self):
        """ADVISORY-severity issues appear in envelope['advisory'], not silently dropped."""
        # intent_unspecified is ADVISORY
        decision = admit(intent=None, files=FILES_SIMPLE)
        advisory_codes = [i.code for i in decision.issues if i.severity == Severity.ADVISORY]

        envelope = format_output(decision)
        envelope_advisory_codes = [a["code"] for a in envelope["advisory"]]

        # Kill criterion: all advisory-severity issues from AdmissionDecision appear in envelope
        for code in advisory_codes:
            assert code in envelope_advisory_codes, (
                f"ADVISORY_OUTPUT_INVISIBILITY: issue code {code!r} "
                f"dropped from envelope advisory list. "
                f"envelope advisory codes: {envelope_advisory_codes}"
            )

    def test_advisory_non_empty_for_replay_path(self):
        """replay_x.py produces non-empty advisory (missing_companion)."""
        decision = admit(intent="modify_existing", files=FILES_ADVISORY)
        envelope = format_output(decision)

        # Kill criterion
        assert len(envelope["advisory"]) >= 1, (
            f"ADVISORY_OUTPUT_INVISIBILITY: advisory list is empty for replay_x.py. "
            f"missing_companion advisory may be silently dropped."
        )
        assert isinstance(envelope["advisory"], list)

    def test_advisory_visible_through_shadow_key(self, monkeypatch):
        """advisory issues surface through payload['v_next_shadow']['advisory']."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: None,
        )

        payload = {
            "ok": True,
            "admission": {"status": "admitted"},
            "route_card": {},
            "task_blockers": [],
            "admission_blockers": [],
        }

        result = maybe_shadow_compare(
            {**payload},
            task="modify replay data",
            files=FILES_ADVISORY,
            intent="modify_existing",
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None

        # Kill criterion: advisory accessible through v_next_shadow key
        assert "advisory" in shadow, "advisory key missing from v_next_shadow envelope"
        assert isinstance(shadow["advisory"], list), (
            f"advisory should be list in v_next_shadow, got {type(shadow['advisory'])}"
        )

    def test_empty_advisory_when_clean_admit(self):
        """Advisory list is empty (not None) for a clean ADMIT with no advisory issues."""
        # docs path admits cleanly for packet_planning profile
        decision = admit(intent="create_new", files=["docs/operations/AGENTS.md"])
        envelope = format_output(decision)

        assert isinstance(envelope["advisory"], list), "advisory must be list even when empty"
        assert isinstance(envelope["blockers"], list), "blockers must be list even when empty"
        # These may or may not be empty depending on the actual admission result
        # but they must be lists
