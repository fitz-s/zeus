# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe14
"""
Probe 14 — Analyzer aggregation correctness (dual-metric P4 gate).

Trigger: synthesize 1000-record fixture covering AGREE/DISAGREE mix + 150 SKIP_HONORED.
Run divergence_summary.aggregate() and verify:
1. Per-profile agreement_pct excludes SKIP_HONORED from denominator.
2. skip_honored_rate = 0.15 (150/1000).
3. p4_gate_ok reflects both metrics (agreement_pct >= 0.95 AND skip_honored_rate < 0.20).
4. A second fixture with skip_honored_rate=0.25 sets p4_gate_ok=False even if
   all non-skip records AGREE.

Kill criteria (all three must pass):
1. agreement_pct matches expected per-profile value excluding SKIP_HONORED.
2. abs(skip_honored_rate - 0.15) < 0.001.
3. p4_gate_ok = (all_profiles_above_95 and 0.15 < 0.20).
"""
import json
from datetime import date
from pathlib import Path

import pytest

from scripts.topology_v_next.divergence_logger import DivergenceRecord, log_divergence, daily_path
from scripts.topology_v_next.divergence_summary import aggregate


def _make_record(
    agreement_class: str,
    profile_new: str = "profile_a",
    old_admit_status: str = "admitted",
    new_admit_severity: str = "ADMIT",
) -> DivergenceRecord:
    companion_skip = agreement_class == "SKIP_HONORED"
    return DivergenceRecord(
        ts="2026-05-15T12:00:00.000Z",
        schema_version="1",
        event_type="companion_skip_honored" if companion_skip else (
            "agree" if agreement_class == "AGREE" else "divergence_observation"
        ),
        profile_resolved_old=profile_new,
        profile_resolved_new=profile_new,
        intent_typed="modify_existing",
        intent_supplied="modify_existing",
        files=["src/foo.py"],
        old_admit_status=old_admit_status,
        new_admit_severity=new_admit_severity,
        new_admit_ok=(new_admit_severity in {"ADMIT", "ADVISORY"}),
        agreement_class=agreement_class,
        friction_pattern_hit=None,
        missing_companion=[],
        companion_skip_used=companion_skip,
        closest_rejected_profile=None,
        kernel_alert_count=0,
        friction_budget_used=1,
        task_hash="aabb1122ccdd3344",
        error=None,
    )


class TestProbe14AnalyzerAggregationCorrectness:

    def _write_fixture(self, tmp_path, records: list[DivergenceRecord]) -> date:
        """Write all records to a single daily JSONL file and return the date."""
        day = date(2026, 5, 15)
        path = daily_path(root=tmp_path, today=day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for r in records:
                # Manually serialize to avoid calling log_divergence (avoids live fs side effects)
                import dataclasses
                d = dataclasses.asdict(r)
                f.write(json.dumps(d, default=str) + "\n")
        return day

    def test_1000_record_fixture_dual_metric(self, tmp_path):
        """1000-record fixture: 150 SKIP_HONORED, 800 AGREE, 50 DISAGREE_SEVERITY."""
        n_agree = 800
        n_disagree = 50
        n_skip = 150
        records = (
            [_make_record("AGREE") for _ in range(n_agree)] +
            [_make_record("DISAGREE_SEVERITY", new_admit_severity="ADVISORY") for _ in range(n_disagree)] +
            [_make_record("SKIP_HONORED") for _ in range(n_skip)]
        )

        day = self._write_fixture(tmp_path, records)
        summary = aggregate(day, day, root=tmp_path)

        # Kill criterion 1: agreement_pct excludes SKIP_HONORED denominator
        # Non-skip records: 800 AGREE + 50 DISAGREE = 850; pct = 800/850
        expected_pct = 800 / 850
        actual_pct = summary["per_profile_agreement"].get("profile_a")
        assert actual_pct is not None, "profile_a not in agreement_pct_excluding_skips"
        assert abs(actual_pct - expected_pct) < 0.001, (
            f"Kill criterion 1 failed: expected agreement_pct={expected_pct:.4f}, "
            f"got {actual_pct:.4f}. SKIP_HONORED may be leaking into denominator."
        )

        # Kill criterion 2: skip_honored_rate = 150/1000 = 0.15
        assert abs(summary["skip_honored_rate"] - 0.15) < 0.001, (
            f"Kill criterion 2 failed: skip_honored_rate should be 0.15, "
            f"got {summary['skip_honored_rate']:.4f}"
        )

        # Kill criterion 3: p4_gate_ok: profile pct is 800/850 ≈ 0.941 < 0.95 → False
        assert summary["p4_gate_ok"] is False, (
            f"p4_gate_ok should be False (profile pct {expected_pct:.4f} < 0.95)"
        )

    def test_high_skip_rate_blocks_p4_gate(self, tmp_path):
        """A fixture with skip_honored_rate=0.25 blocks P4 gate even if all non-skip AGREE."""
        n_agree = 750
        n_skip = 250
        records = (
            [_make_record("AGREE") for _ in range(n_agree)] +
            [_make_record("SKIP_HONORED") for _ in range(n_skip)]
        )

        day = self._write_fixture(tmp_path, records)
        summary = aggregate(day, day, root=tmp_path)

        # Agreement pct = 750/750 = 1.0 (all non-skip agree)
        actual_pct = summary["per_profile_agreement"].get("profile_a")
        assert actual_pct is not None
        assert abs(actual_pct - 1.0) < 0.001, (
            f"All non-skip records agree, expected pct=1.0, got {actual_pct:.4f}"
        )

        # Skip rate = 250/1000 = 0.25 > 0.20 → p4_gate_ok = False
        assert abs(summary["skip_honored_rate"] - 0.25) < 0.001
        assert summary["p4_gate_ok"] is False, (
            f"p4_gate_ok must be False when skip_honored_rate=0.25 >= 0.20. "
            f"High skip rate masks real divergence."
        )

    def test_all_agree_low_skip_gates_p4(self, tmp_path):
        """Fixture where agreement_pct >= 0.95 AND skip_honored_rate < 0.20 unlocks p4_gate_ok."""
        # Use 500+ records to satisfy sample_size threshold
        n_agree = 500
        n_skip = 50  # 50/550 = 0.09 < 0.20
        records = (
            [_make_record("AGREE") for _ in range(n_agree)] +
            [_make_record("SKIP_HONORED") for _ in range(n_skip)]
        )

        day = self._write_fixture(tmp_path, records)
        summary = aggregate(day, day, root=tmp_path)

        actual_pct = summary["per_profile_agreement"].get("profile_a")
        assert actual_pct is not None
        assert abs(actual_pct - 1.0) < 0.001

        assert summary["skip_honored_rate"] < 0.20
        # With >= 500 records, >= 95% agreement, skip_rate < 20% → p4_gate_ok = True
        assert summary["p4_gate_ok"] is True, (
            f"p4_gate_ok should be True for perfect agreement + low skip rate. "
            f"agreement_pct={actual_pct:.4f}, skip_honored_rate={summary['skip_honored_rate']:.4f}"
        )
