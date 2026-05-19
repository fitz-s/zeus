# Created: 2026-05-15
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe6
#                  operator directive 2026-05-19 (hard_stop → advisory-only)
"""
Probe 6 — Live-money surface advisory (previously HARD_STOP divergence).

Trigger: files=["src/execution/executor.py"] (LIVE_SIDE_EFFECT_PATH per ZEUS_BINDING
hard_stop_paths: "src/execution/**") with intent="modify_existing".

Operator directive 2026-05-19: hard_stop paths now produce ADVISORY context, not
HARD_STOP admission denial. kernel_alerts still capture the HARD_STOP kernel match
for downstream critic routing. DivergenceRecord agreement_class changes accordingly.

Kill criterion: kernel_alert_count >= 1 (kernel wiring preserved).
Severity invariant: decision.severity != HARD_STOP (advisory-only directive).
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

    def test_kernel_alerts_still_populated_for_execution_path(self):
        """Kernel still captures hard_stop match even under advisory-only directive.
        kernel_alert_count >= 1 is the preserved kill criterion.
        Operator directive 2026-05-19: severity is no longer HARD_STOP."""
        decision = admit(intent="modify_existing", files=FILES)
        assert len(decision.kernel_alerts) >= 1, (
            f"Expected >= 1 kernel alert, got {len(decision.kernel_alerts)}. "
            f"Hard Safety Kernel wiring may be broken."
        )
        # Operator directive: hard_stop must NOT block (HARD_STOP severity removed)
        assert decision.severity.value != "HARD_STOP", (
            f"Operator directive 2026-05-19: execution path should advise not block. "
            f"Got {decision.severity.value!r}."
        )

    def test_live_money_advisory_emitted_for_execution_path(self):
        """format_output surfaces live_money_surface_touched in advisory (not blockers).
        Operator directive 2026-05-19: the envelope decision reflects advisory severity."""
        decision = admit(intent="modify_existing", files=FILES)
        envelope = format_output(decision)

        # Kernel alert is still present
        assert len(envelope["kernel_alerts"]) >= 1, (
            f"kernel_alerts not in envelope: {envelope['kernel_alerts']}"
        )
        # Decision is no longer HARD_STOP
        assert envelope["decision"] != "HARD_STOP", (
            f"Operator directive 2026-05-19: envelope decision must not be HARD_STOP. "
            f"Got {envelope['decision']!r}"
        )

    def test_divergence_record_kernel_alert_count(self, monkeypatch):
        """Shadow record kernel_alert_count >= 1 for execution path (kill criterion preserved)."""
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

        assert len(captured_records) == 1
        record = captured_records[0]

        # Kill criterion preserved: kernel_alert_count >= 1
        assert record.kernel_alert_count >= 1, (
            f"kernel_alert_count == {record.kernel_alert_count}; kernel wiring broken."
        )

    def test_envelope_kernel_alerts_populated(self, monkeypatch):
        """kernel_alerts in envelope is non-empty for execution path."""
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
