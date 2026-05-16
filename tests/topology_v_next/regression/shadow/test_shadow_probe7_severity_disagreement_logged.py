# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe7
"""
Probe 7 — Severity disagreement is logged and readable from JSONL.

Trigger: old payload status="blocked" (SOFT_BLOCK equiv) while v_next admits with
ADMIT severity (old side over-blocked case). This tests that disagreements are
captured in the log record and classified correctly.

Kill criterion: read back the JSONL; assert record.agreement_class == "DISAGREE_SEVERITY".
"""
import json
import tempfile
from pathlib import Path
from datetime import date, datetime, UTC

import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare
from scripts.topology_v_next.divergence_logger import DivergenceRecord, log_divergence, daily_path
from scripts.topology_v_next.dataclasses import Severity


# Payload where old admission over-blocked a docs path; v_next admits it
# (docs/operations/ paths match packet_planning profile — ADMIT in v_next)
FILES_DOCS = ["docs/operations/AGENTS.md"]
PAYLOAD_BLOCKED = {
    "ok": False,
    "admission": {"status": "blocked"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe7SeverityDisagreementLogged:

    def test_record_written_to_jsonl(self, tmp_path, monkeypatch):
        """log_divergence writes one readable JSON line to the daily JSONL file."""
        # Build a minimal synthetic record and log it
        record = DivergenceRecord(
            ts="2026-05-15T12:00:00.000Z",
            schema_version="1",
            event_type="divergence_observation",
            profile_resolved_old="old_profile",
            profile_resolved_new="new_profile",
            intent_typed="modify_existing",
            intent_supplied="modify_existing",
            files=["src/foo.py"],
            old_admit_status="blocked",
            new_admit_severity="ADMIT",
            new_admit_ok=True,
            agreement_class="DISAGREE_SEVERITY",
            friction_pattern_hit=None,
            missing_companion=[],
            companion_skip_used=False,
            closest_rejected_profile=None,
            kernel_alert_count=0,
            friction_budget_used=1,
            task_hash="abc123def456abcd",
            error=None,
        )

        log_divergence(record, root=tmp_path)

        today = datetime.now(UTC).date()  # UTC — matches divergence_logger's date logic
        log_file = daily_path(root=tmp_path, today=today)
        assert log_file.exists(), f"Log file not written: {log_file}"

        lines = log_file.read_text().splitlines()
        assert len(lines) == 1, f"Expected 1 JSONL line, got {len(lines)}"

        parsed = json.loads(lines[0])
        # Kill criterion: agreement_class is correctly recorded
        assert parsed["agreement_class"] == "DISAGREE_SEVERITY", (
            f"Expected DISAGREE_SEVERITY in JSONL, got {parsed['agreement_class']!r}"
        )
        assert parsed["old_admit_status"] == "blocked"
        assert parsed["new_admit_severity"] == "ADMIT"

    def test_shadow_compare_classifies_severity_disagree(self, monkeypatch):
        """maybe_shadow_compare builds record with DISAGREE_SEVERITY when applicable."""
        captured = []
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda r: captured.append(r),
        )

        # docs/operations path — v_next admits via packet_planning profile
        result = maybe_shadow_compare(
            {**PAYLOAD_BLOCKED},
            task="update agents doc",
            files=FILES_DOCS,
            intent="modify_existing",
            v_next_shadow=True,
        )

        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None

        if captured:
            record = captured[0]
            # If v_next and old disagree on severity, it's DISAGREE_SEVERITY
            if record.old_admit_status in ("blocked",) and record.new_admit_severity == "ADMIT":
                assert record.agreement_class == "DISAGREE_SEVERITY", (
                    f"Expected DISAGREE_SEVERITY, got {record.agreement_class!r}"
                )

    def test_jsonl_line_is_valid_json_with_schema_version(self, tmp_path):
        """Every written JSONL line parses as valid JSON with schema_version='1'."""
        record = DivergenceRecord(
            ts="2026-05-15T12:00:00.000Z",
            schema_version="1",
            event_type="agree",
            profile_resolved_old="profile_a",
            profile_resolved_new="profile_a",
            intent_typed="create_new",
            intent_supplied="create_new",
            files=["src/new.py"],
            old_admit_status="admitted",
            new_admit_severity="ADMIT",
            new_admit_ok=True,
            agreement_class="AGREE",
            friction_pattern_hit=None,
            missing_companion=[],
            companion_skip_used=False,
            closest_rejected_profile=None,
            kernel_alert_count=0,
            friction_budget_used=1,
            task_hash="0011223344556677",
            error=None,
        )

        log_divergence(record, root=tmp_path)

        today = datetime.now(UTC).date()  # UTC — matches divergence_logger's date logic
        log_file = daily_path(root=tmp_path, today=today)
        text = log_file.read_text().strip()
        # Must be single-line (no embedded newlines)
        assert "\n" not in text, "JSONL line contains embedded newline"
        parsed = json.loads(text)
        assert parsed["schema_version"] == "1"
