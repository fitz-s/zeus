# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe11
"""
Probe 11 — Kernel alerts disagree: v_next emits kernel alerts for hard_stop paths.

Trigger: files=["config/credentials/api_key.json"] (config/credentials/** is a
hard_stop_path in ZEUS_BINDING §3). Old side returns admitted; v_next emits HARD_STOP.

Kill criterion: assert record.kernel_alert_count >= 1
— failure means kernel wiring is broken for credential paths.
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit


FILES_CRED = ["config/credentials/api_key.json"]
PAYLOAD_ADMITTED = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe11KernelAlertsDisagreement:

    def test_credentials_path_emits_hard_stop(self):
        """v_next emits HARD_STOP + kernel alert for credentials path."""
        decision = admit(intent="modify_existing", files=FILES_CRED)
        assert decision.severity.value == "HARD_STOP", (
            f"Expected HARD_STOP for credentials path. Got {decision.severity.value!r}"
        )
        # Kill criterion
        assert len(decision.kernel_alerts) >= 1, (
            f"kernel_alerts empty for credentials path — kernel wiring broken."
        )

    def test_kernel_alerts_appear_in_envelope(self):
        """format_output surfaces kernel_alerts from AdmissionDecision."""
        decision = admit(intent="modify_existing", files=FILES_CRED)
        envelope = format_output(decision)

        # Kill criterion: kernel_alerts present in envelope
        assert len(envelope["kernel_alerts"]) >= 1, (
            f"kernel_alerts not surfaced in envelope: {envelope['kernel_alerts']}"
        )
        # Each kernel alert is a dict (from to_dict())
        for alert in envelope["kernel_alerts"]:
            assert isinstance(alert, dict)
            assert "code" in alert

    def test_shadow_record_kernel_alert_count(self, monkeypatch):
        """DivergenceRecord.kernel_alert_count is populated correctly."""
        captured = []
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: captured.append(r),
        )

        result = maybe_shadow_compare(
            {**PAYLOAD_ADMITTED},
            task="update api key",
            files=FILES_CRED,
            intent="modify_existing",
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None

        assert len(captured) == 1
        record = captured[0]

        # Kill criterion
        assert record.kernel_alert_count >= 1, (
            f"kernel_alert_count == {record.kernel_alert_count}. "
            f"Kernel wiring is broken for credential paths."
        )
        assert record.agreement_class == "DISAGREE_HARD_STOP"

    def test_hard_stop_blockers_not_in_advisory(self):
        """HARD_STOP issues appear in blockers, not advisory."""
        decision = admit(intent="modify_existing", files=FILES_CRED)
        envelope = format_output(decision)

        # All kernel issues should be HARD_STOP → in blockers
        blocker_codes = [b["code"] for b in envelope["blockers"]]
        assert len(blocker_codes) >= 1
        advisory_codes = [a["code"] for a in envelope["advisory"]]
        # hard_stop_path issues should NOT appear in advisory
        assert "hard_stop_path" not in advisory_codes, (
            f"hard_stop_path appeared in advisory (should be in blockers): {advisory_codes}"
        )
