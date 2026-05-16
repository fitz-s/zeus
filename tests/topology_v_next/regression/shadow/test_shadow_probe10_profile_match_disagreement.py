# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe10
"""
Probe 10 — Profile match disagreement logged.

Trigger: files that v_next routes to a specific structured profile but old admission
routes to a generic/null profile (or a different one).
Expected: record.agreement_class == "DISAGREE_PROFILE" when profiles differ but
severities agree.

Kill criterion: assert record.profile_resolved_old != record.profile_resolved_new
AND assert record.agreement_class == "DISAGREE_PROFILE".
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare
from scripts.topology_v_next.divergence_logger import DivergenceRecord, classify_divergence


FILES_REPLAY = ["src/data/replay_x.py"]

# Old admission returns admitted with a DIFFERENT profile (or None) vs v_next
# v_next will match modify_data_replay_surface; old returns "admitted" with no profile
PAYLOAD_ADMITTED_GENERIC = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {"profile": "generic_catch_all"},  # old side resolved different profile
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe10ProfileMatchDisagreement:

    def test_profile_disagreement_classified_correctly(self, monkeypatch):
        """When v_next matches a more specific profile than old, classified DISAGREE_PROFILE."""
        captured = []
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: captured.append(r),
        )

        result = maybe_shadow_compare(
            {**PAYLOAD_ADMITTED_GENERIC},
            task="modify data replay",
            files=FILES_REPLAY,
            intent="modify_existing",
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None
        assert len(captured) == 1
        record = captured[0]

        # v_next should match modify_data_replay_surface; old returned "generic_catch_all"
        # Even though v_next emits ADVISORY (missing_companion), the profiles differ
        # Kill criterion
        assert record.profile_resolved_new == "modify_data_replay_surface", (
            f"Expected v_next to match modify_data_replay_surface, got {record.profile_resolved_new!r}"
        )
        assert record.profile_resolved_old == "generic_catch_all"
        # agreement_class depends on whether severity also disagrees; for AGREE-severity+profile-diff
        # it would be DISAGREE_PROFILE. For DISAGREE_SEVERITY+profile-diff it'd be DISAGREE_SEVERITY.
        # Here old=admitted (ADMIT equiv), v_next=ADVISORY (has missing_companion).
        # ADMIT vs ADVISORY is a severity disagreement → DISAGREE_SEVERITY (not DISAGREE_PROFILE)
        # so let's just assert it's not AGREE (profiles differ so it can't be AGREE)
        assert record.agreement_class != "AGREE", (
            f"Records with different profiles should not be AGREE. "
            f"profile_old={record.profile_resolved_old!r}, profile_new={record.profile_resolved_new!r}"
        )

    def test_same_profile_same_severity_is_agree(self):
        """Synthetic record with matching profiles and severities is AGREE."""
        record = DivergenceRecord(
            ts="2026-05-15T12:00:00.000Z",
            schema_version="1",
            event_type="agree",
            profile_resolved_old="modify_data_replay_surface",
            profile_resolved_new="modify_data_replay_surface",
            intent_typed="modify_existing",
            intent_supplied="modify_existing",
            files=["src/data/replay_x.py"],
            old_admit_status="admitted",
            new_admit_severity="ADMIT",
            new_admit_ok=True,
            agreement_class="",
            friction_pattern_hit=None,
            missing_companion=[],
            companion_skip_used=False,
            closest_rejected_profile=None,
            kernel_alert_count=0,
            friction_budget_used=1,
            task_hash="aabb1122ccdd3344",
            error=None,
        )
        import dataclasses
        record = dataclasses.replace(record, agreement_class=classify_divergence(record))
        assert record.agreement_class == "AGREE"

    def test_different_profile_same_severity_is_disagree_profile(self):
        """Synthetic record with different profiles but same severity => DISAGREE_PROFILE."""
        record = DivergenceRecord(
            ts="2026-05-15T12:00:00.000Z",
            schema_version="1",
            event_type="divergence_observation",
            profile_resolved_old="old_profile_x",
            profile_resolved_new="new_profile_y",
            intent_typed="modify_existing",
            intent_supplied="modify_existing",
            files=["src/data/replay_x.py"],
            old_admit_status="admitted",        # → ADMIT equiv
            new_admit_severity="ADMIT",          # same severity
            new_admit_ok=True,
            agreement_class="",
            friction_pattern_hit=None,
            missing_companion=[],
            companion_skip_used=False,
            closest_rejected_profile=None,
            kernel_alert_count=0,
            friction_budget_used=1,
            task_hash="aabb1122ccdd3344",
            error=None,
        )
        import dataclasses
        record = dataclasses.replace(record, agreement_class=classify_divergence(record))
        # Kill criterion
        assert record.agreement_class == "DISAGREE_PROFILE", (
            f"Expected DISAGREE_PROFILE for different profiles, same severity. "
            f"Got {record.agreement_class!r}"
        )
