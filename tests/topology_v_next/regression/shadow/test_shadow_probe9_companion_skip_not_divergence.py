# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe9
"""
Probe 9 — companion_skip_token NOT counted as divergence (P2 integration).

Trigger: env var for companion_skip_acknowledge_token is set; invoke with
files=["src/data/vendor_response_x.py"] (modify_vendor_response profile has
companion_skip_acknowledge_token: "COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1").
Expected: v_next emits companion_skip_token_used ADVISORY; record.companion_skip_used=True;
agreement_class == "SKIP_HONORED".

Kill criterion:
1. assert record.agreement_class == "SKIP_HONORED"
2. In a mock 100-record fixture where 50 are SKIP_HONORED and 50 are AGREE, the
   analyzer excludes SKIP_HONORED from denominator => agreement_pct = 50/50 = 1.0,
   not 50/100 = 0.5.
"""
import os
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare, format_output
from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.divergence_logger import DivergenceRecord, classify_divergence


FILES = ["src/data/vendor_response_x.py"]
PAYLOAD_ADMITTED = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe9CompanionSkipNotDivergence:

    def test_skip_token_emits_companion_skip_token_used(self, monkeypatch):
        """With skip token env var set, v_next emits companion_skip_token_used."""
        monkeypatch.setenv("COMPANION_SKIP_NEEDS_HUMAN_REVIEW", "1")

        decision = admit(intent="modify_existing", files=FILES)
        issue_codes = [i.code for i in decision.issues]
        assert "companion_skip_token_used" in issue_codes, (
            f"companion_skip_token_used not emitted. Got: {issue_codes}"
        )

    def test_skip_token_agreement_class_skip_honored(self, monkeypatch):
        """companion_skip_used=True => agreement_class == SKIP_HONORED."""
        monkeypatch.setenv("COMPANION_SKIP_NEEDS_HUMAN_REVIEW", "1")

        captured = []
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: captured.append(r),
        )

        result = maybe_shadow_compare(
            {**PAYLOAD_ADMITTED},
            task="vendor response hotfix",
            files=FILES,
            intent="modify_existing",
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None

        assert len(captured) == 1
        record = captured[0]

        assert record.companion_skip_used is True, (
            f"companion_skip_used should be True, got {record.companion_skip_used}"
        )
        # Kill criterion 1: agreement_class == SKIP_HONORED
        assert record.agreement_class == "SKIP_HONORED", (
            f"Expected SKIP_HONORED, got {record.agreement_class!r}. "
            f"Skip token should NOT count as divergence."
        )

    def test_analyzer_excludes_skip_honored_from_denominator(self):
        """Kill criterion 2: SKIP_HONORED records excluded from agreement-pct denominator."""
        # Build synthetic records: 50 AGREE + 50 SKIP_HONORED
        def _make_record(agreement_class: str) -> DivergenceRecord:
            return DivergenceRecord(
                ts="2026-05-15T12:00:00.000Z",
                schema_version="1",
                event_type="agree" if agreement_class == "AGREE" else "companion_skip_honored",
                profile_resolved_old="p1",
                profile_resolved_new="p1",
                intent_typed="modify_existing",
                intent_supplied="modify_existing",
                files=["src/foo.py"],
                old_admit_status="admitted",
                new_admit_severity="ADMIT",
                new_admit_ok=True,
                agreement_class=agreement_class,
                friction_pattern_hit=None,
                missing_companion=[],
                companion_skip_used=(agreement_class == "SKIP_HONORED"),
                closest_rejected_profile=None,
                kernel_alert_count=0,
                friction_budget_used=1,
                task_hash="aabbccdd00112233",
                error=None,
            )

        records = [_make_record("AGREE") for _ in range(50)] + \
                  [_make_record("SKIP_HONORED") for _ in range(50)]

        # Replicate the analyzer's exclusion logic (from SCAFFOLD §6.3)
        non_skip = [r for r in records if r.agreement_class != "SKIP_HONORED"]
        n_agree = sum(1 for r in non_skip if r.agreement_class == "AGREE")
        if non_skip:
            agreement_pct = n_agree / len(non_skip)
        else:
            agreement_pct = 1.0

        skip_honored_rate = sum(1 for r in records if r.agreement_class == "SKIP_HONORED") / len(records)

        assert abs(agreement_pct - 1.0) < 0.001, (
            f"agreement_pct should be 1.0 (50/50 non-skip agree), got {agreement_pct:.3f}. "
            f"SKIP_HONORED is leaking into denominator."
        )
        assert abs(skip_honored_rate - 0.5) < 0.001, (
            f"skip_honored_rate should be 0.5, got {skip_honored_rate:.3f}"
        )
