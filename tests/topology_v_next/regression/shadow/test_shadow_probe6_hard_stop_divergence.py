# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe6
"""
Probe 6 — HARD_STOP divergence captured in shadow record.

Trigger: files=["src/execution/executor.py"] (LIVE_SIDE_EFFECT_PATH per ZEUS_BINDING
hard_stop_paths: "src/execution/**") with intent="modify_existing".
Expected: v_next emits HARD_STOP via kernel; kernel_alert_count >= 1;
old side (stub payload) returns its normal admission result.
DivergenceRecord is classified DISAGREE_HARD_STOP.

Kill criterion: assert record.agreement_class == "DISAGREE_HARD_STOP"
AND assert kernel_alert_count >= 1.
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.divergence_logger import DivergenceRecord


FILES = ["src/execution/executor.py"]

# Old admission returns "admitted" — v_next sees HARD_STOP; this IS the divergence
PAYLOAD_ADMITTED = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe6HardStopDivergence:

    def test_hard_stop_emitted_by_vnext(self):
        """v_next emits HARD_STOP for src/execution/executor.py."""
        decision = admit(intent="modify_existing", files=FILES)
        assert decision.severity.value == "HARD_STOP", (
            f"Expected HARD_STOP severity, got {decision.severity.value!r}. "
            f"Hard Safety Kernel wiring may be broken."
        )
        assert len(decision.kernel_alerts) >= 1, (
            f"Expected >= 1 kernel alert, got {len(decision.kernel_alerts)}"
        )

    def test_hard_stop_envelope_blockers_populated(self):
        """format_output populates blockers list when v_next emits HARD_STOP."""
        decision = admit(intent="modify_existing", files=FILES)
        envelope = format_output(decision)

        assert envelope["decision"] == "HARD_STOP"
        assert envelope["ok"] is False
        # SCAFFOLD §2.1: blockers = decision.issues only; kernel HARD_STOP lives in kernel_alerts.
        # For kernel-only HARD_STOP paths, blockers may be empty — kernel_alerts carries evidence.
        assert len(envelope["blockers"]) + len(envelope["kernel_alerts"]) >= 1, (
            f"Both blockers and kernel_alerts are empty despite HARD_STOP severity. "
            f"blockers={envelope['blockers']}, kernel_alerts={envelope['kernel_alerts']}"
        )
        assert envelope["advisory"] == [], (
            f"advisory should be empty for HARD_STOP: {envelope['advisory']}"
        )

    def test_divergence_record_agreement_class(self, monkeypatch):
        """Shadow record classified DISAGREE_HARD_STOP when v_next sees HARD_STOP."""
        captured_records = []
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: captured_records.append(record),
        )

        result = maybe_shadow_compare(
            {**PAYLOAD_ADMITTED},
            task="modify executor",
            files=FILES,
            intent="modify_existing",
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None

        # Kill criterion: record must be classified DISAGREE_HARD_STOP
        assert len(captured_records) == 1
        record = captured_records[0]
        assert record.agreement_class == "DISAGREE_HARD_STOP", (
            f"Expected DISAGREE_HARD_STOP, got {record.agreement_class!r}. "
            f"Hard Stop divergence classification broken."
        )

        # Kill criterion: kernel_alert_count >= 1
        assert record.kernel_alert_count >= 1, (
            f"kernel_alert_count == {record.kernel_alert_count}; kernel wiring broken."
        )

    def test_envelope_kernel_alerts_populated(self, monkeypatch):
        """kernel_alerts in envelope is non-empty for HARD_STOP path."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        result = maybe_shadow_compare(
            {**PAYLOAD_ADMITTED},
            task="modify executor",
            files=FILES,
            intent="modify_existing",
            v_next_shadow=True,
        )
        shadow = result["v_next_shadow"]
        assert len(shadow["kernel_alerts"]) >= 1
