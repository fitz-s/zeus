# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md
#                  §7 P3.2 (test list), §6 (P4 gate), §6.3 (dual metric),
#                  §6.4 (per-friction-pattern)
"""
Tests for scripts/topology_v_next/divergence_summary.py (P3.2).

Coverage per SCAFFOLD §7 P3.2:
  - aggregate fixture (mixed AGREE/DISAGREE/SKIP_HONORED)
  - skip exclusion from agreement-% denominator
  - per-friction-pattern counting
  - CLI argparse combinations
  - sample-size tier labels
  - malformed-JSON line handling
  - p4_gate_ok True/False per condition
  - write_summary atomic write verification
  - integration: log via P3.1 then aggregate via P3.2
"""
from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from scripts.topology_v_next.dataclasses import FrictionPattern, Severity
from scripts.topology_v_next.divergence_logger import (
    DivergenceRecord,
    log_divergence,
)
from scripts.topology_v_next.divergence_summary import (
    SummaryReport,
    _aggregate_records,
    _compute_per_friction_pattern,
    _compute_per_profile_agreement,
    _load_window,
    _sample_size_tier,
    aggregate,
    cli_main,
    load_window,
    write_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULTS: dict = dict(
    ts="2026-05-15T00:00:00.000Z",
    schema_version="1",
    event_type="divergence_observation",
    agreement_class="AGREE",
    profile_resolved_old="profile_a",
    old_admit_status="admitted",
    profile_resolved_new="profile_a",
    new_admit_severity="ADMIT",
    new_admit_ok=True,
    intent_typed="modify_existing",
    intent_supplied=None,
    files=("src/foo.py",),
    missing_companion=(),
    companion_skip_used=False,
    friction_pattern_hit=None,
    closest_rejected_profile=None,
    kernel_alert_count=0,
    friction_budget_used=0,
    task_hash="abcdef1234567890",
    error=None,
)


def _make_record(**overrides) -> DivergenceRecord:
    """Build a DivergenceRecord with sensible defaults; override specific fields."""
    data = {**_DEFAULTS, **overrides}
    return DivergenceRecord(**data)


def _make_records(n: int, **overrides) -> list[DivergenceRecord]:
    """Return a list of n identical records."""
    return [_make_record(**overrides) for _ in range(n)]


# ---------------------------------------------------------------------------
# TestSampleSizeTier
# ---------------------------------------------------------------------------

class TestSampleSizeTier:
    def test_insufficient_zero(self):
        assert _sample_size_tier(0) == "insufficient"

    def test_insufficient_boundary(self):
        assert _sample_size_tier(99) == "insufficient"

    def test_marginal_lower_boundary(self):
        assert _sample_size_tier(100) == "marginal"

    def test_marginal_upper_boundary(self):
        assert _sample_size_tier(499) == "marginal"

    def test_sufficient_lower_boundary(self):
        assert _sample_size_tier(500) == "sufficient"

    def test_sufficient_large(self):
        assert _sample_size_tier(10000) == "sufficient"


# ---------------------------------------------------------------------------
# TestComputePerFrictionPattern
# ---------------------------------------------------------------------------

class TestComputePerFrictionPattern:
    def test_empty_records_all_zeros(self):
        result = _compute_per_friction_pattern([])
        assert set(result.keys()) == {fp.value for fp in FrictionPattern}
        assert all(v == 0 for v in result.values())

    def test_counts_known_pattern(self):
        records = [
            _make_record(friction_pattern_hit=FrictionPattern.LEXICAL_PROFILE_MISS.value),
            _make_record(friction_pattern_hit=FrictionPattern.LEXICAL_PROFILE_MISS.value),
            _make_record(friction_pattern_hit=FrictionPattern.SLICING_PRESSURE.value),
            _make_record(friction_pattern_hit=None),
        ]
        result = _compute_per_friction_pattern(records)
        assert result[FrictionPattern.LEXICAL_PROFILE_MISS.value] == 2
        assert result[FrictionPattern.SLICING_PRESSURE.value] == 1
        assert result[FrictionPattern.UNION_SCOPE_EXPANSION.value] == 0

    def test_ignores_none_friction_hit(self):
        records = _make_records(10, friction_pattern_hit=None)
        result = _compute_per_friction_pattern(records)
        assert all(v == 0 for v in result.values())

    def test_all_seven_patterns_present_in_output(self):
        result = _compute_per_friction_pattern([])
        assert len(result) == len(FrictionPattern)


# ---------------------------------------------------------------------------
# TestComputePerProfileAgreement
# ---------------------------------------------------------------------------

class TestComputePerProfileAgreement:
    def test_single_profile_perfect_agreement(self):
        records = _make_records(10, agreement_class="AGREE", profile_resolved_new="p1")
        result = _compute_per_profile_agreement(records)
        assert result == {"p1": 1.0}

    def test_single_profile_zero_agreement(self):
        records = _make_records(10, agreement_class="DISAGREE_SEVERITY", profile_resolved_new="p1")
        result = _compute_per_profile_agreement(records)
        assert result == {"p1": 0.0}

    def test_skip_honored_excluded_from_denominator(self):
        records = [
            _make_record(agreement_class="AGREE", profile_resolved_new="p1"),
            _make_record(agreement_class="SKIP_HONORED", profile_resolved_new="p1"),
            _make_record(agreement_class="SKIP_HONORED", profile_resolved_new="p1"),
        ]
        result = _compute_per_profile_agreement(records, exclude_skip_honored=True)
        # Only 1 eligible (AGREE), denominator = 1
        assert result["p1"] == pytest.approx(1.0)

    def test_error_excluded_from_denominator(self):
        records = [
            _make_record(agreement_class="AGREE", profile_resolved_new="p1"),
            _make_record(agreement_class="ERROR", profile_resolved_new="p1"),
        ]
        result = _compute_per_profile_agreement(records)
        assert result["p1"] == pytest.approx(1.0)

    def test_none_profile_resolved_new_bucketed_separately(self):
        records = [
            _make_record(agreement_class="AGREE", profile_resolved_new=None),
        ]
        result = _compute_per_profile_agreement(records)
        assert "__no_profile__" in result
        assert result["__no_profile__"] == pytest.approx(1.0)

    def test_multiple_profiles(self):
        records = [
            _make_record(agreement_class="AGREE", profile_resolved_new="p1"),
            _make_record(agreement_class="AGREE", profile_resolved_new="p1"),
            _make_record(agreement_class="DISAGREE_SEVERITY", profile_resolved_new="p2"),
            _make_record(agreement_class="AGREE", profile_resolved_new="p2"),
        ]
        result = _compute_per_profile_agreement(records)
        assert result["p1"] == pytest.approx(1.0)
        assert result["p2"] == pytest.approx(0.5)

    def test_profile_with_only_skip_returns_none(self):
        records = [
            _make_record(agreement_class="SKIP_HONORED", profile_resolved_new="p_skip"),
        ]
        result = _compute_per_profile_agreement(records, exclude_skip_honored=True)
        # Profile exists but has 0 eligible records → None
        assert result.get("p_skip") is None


# ---------------------------------------------------------------------------
# TestAggregateRecords
# ---------------------------------------------------------------------------

class TestAggregateRecords:
    def _agg(self, records, **kw):
        return _aggregate_records(
            records,
            start_date_iso="2026-05-01",
            end_date_iso="2026-05-15",
            **kw,
        )

    def test_empty_records(self):
        report = self._agg([])
        assert report.total_records == 0
        assert report.overall_agreement_pct_excluding_skips == 0.0
        assert report.skip_honored_rate == 0.0
        assert report.sample_size_tier == "insufficient"
        assert report.p4_gate_ok is False

    def test_all_agree(self):
        records = _make_records(10, agreement_class="AGREE")
        report = self._agg(records)
        assert report.overall_agreement_pct_excluding_skips == pytest.approx(1.0)

    def test_all_disagree(self):
        records = _make_records(10, agreement_class="DISAGREE_SEVERITY")
        report = self._agg(records)
        assert report.overall_agreement_pct_excluding_skips == pytest.approx(0.0)

    def test_mixed_agree_disagree_skip(self):
        records = (
            _make_records(6, agreement_class="AGREE", profile_resolved_new="p1")
            + _make_records(2, agreement_class="DISAGREE_SEVERITY", profile_resolved_new="p1")
            + _make_records(2, agreement_class="SKIP_HONORED", profile_resolved_new="p1")
        )
        report = self._agg(records)
        assert report.total_records == 10
        # skip_honored_rate = 2/10
        assert report.skip_honored_rate == pytest.approx(0.2)
        # eligible denominator = 8 (skip excluded), agree = 6
        assert report.overall_agreement_pct_excluding_skips == pytest.approx(6 / 8)

    def test_date_range_preserved(self):
        report = self._agg([])
        assert report.date_range == ("2026-05-01", "2026-05-15")

    def test_per_friction_all_none(self):
        records = _make_records(5, friction_pattern_hit=None)
        report = self._agg(records)
        assert all(v == 0 for v in report.per_friction_pattern_count.values())

    def test_per_friction_counted(self):
        records = [
            _make_record(friction_pattern_hit=FrictionPattern.PHRASING_GAME_TAX.value),
        ]
        report = self._agg(records)
        assert report.per_friction_pattern_count[FrictionPattern.PHRASING_GAME_TAX.value] == 1

    def test_all_skip_honored(self):
        records = _make_records(200, agreement_class="SKIP_HONORED")
        report = self._agg(records)
        # No eligible records → overall pct = 0
        assert report.overall_agreement_pct_excluding_skips == 0.0
        assert report.skip_honored_rate == pytest.approx(1.0)
        # skip_honored_rate >= 0.20 → p4_gate_ok = False regardless
        assert report.p4_gate_ok is False


# ---------------------------------------------------------------------------
# TestP4Gate
# ---------------------------------------------------------------------------

class TestP4Gate:
    """P4 gate: per-profile agreement >= 0.95 AND skip < 0.20 AND sufficient."""

    def _sufficient_agree_records(self, n: int = 500, n_skip: int = 0) -> list[DivergenceRecord]:
        """Build n records, n_skip of which are SKIP_HONORED, rest AGREE."""
        agree = _make_records(n - n_skip, agreement_class="AGREE", profile_resolved_new="p1")
        skip = _make_records(n_skip, agreement_class="SKIP_HONORED", profile_resolved_new="p1")
        return agree + skip

    def _agg(self, records):
        return _aggregate_records(
            records,
            start_date_iso="2026-05-01",
            end_date_iso="2026-05-15",
        )

    def test_p4_true_when_all_conditions_met(self):
        # 500 records, 495 AGREE + 5 SKIP (1%), skip_rate=1%, per-profile=100%, sufficient
        records = self._sufficient_agree_records(500, n_skip=5)
        report = self._agg(records)
        assert report.sample_size_tier == "sufficient"
        assert report.p4_gate_ok is True

    def test_p4_false_insufficient_sample(self):
        records = _make_records(50, agreement_class="AGREE")
        report = self._agg(records)
        assert report.sample_size_tier == "insufficient"
        assert report.p4_gate_ok is False

    def test_p4_false_marginal_sample(self):
        records = _make_records(200, agreement_class="AGREE")
        report = self._agg(records)
        assert report.sample_size_tier == "marginal"
        assert report.p4_gate_ok is False

    def test_p4_false_skip_rate_too_high(self):
        # 500 records, 100 SKIP (20%), rest AGREE
        records = self._sufficient_agree_records(500, n_skip=100)
        report = self._agg(records)
        assert report.skip_honored_rate == pytest.approx(0.2)
        assert report.p4_gate_ok is False

    def test_p4_false_per_profile_below_threshold(self):
        # Profile p1: 90% agree, profile p2: 100% agree — p1 below 0.95
        p1_agree = _make_records(450, agreement_class="AGREE", profile_resolved_new="p1")
        p1_disagree = _make_records(50, agreement_class="DISAGREE_SEVERITY", profile_resolved_new="p1")
        p2_agree = _make_records(500, agreement_class="AGREE", profile_resolved_new="p2")
        records = p1_agree + p1_disagree + p2_agree
        report = self._agg(records)
        assert report.sample_size_tier == "sufficient"
        # p1 = 0.90, below 0.95 → gate fails
        assert report.per_profile_agreement["p1"] == pytest.approx(0.9)
        assert report.p4_gate_ok is False

    def test_p4_false_all_profiles_below_threshold(self):
        records = _make_records(500, agreement_class="DISAGREE_SEVERITY", profile_resolved_new="p1")
        report = self._agg(records)
        assert report.p4_gate_ok is False

    def test_p4_true_multiple_profiles_all_pass(self):
        # Two profiles each with 500 records, all AGREE
        p1 = _make_records(500, agreement_class="AGREE", profile_resolved_new="p1")
        p2 = _make_records(500, agreement_class="AGREE", profile_resolved_new="p2")
        report = self._agg(p1 + p2)
        assert report.p4_gate_ok is True

    def test_p4_false_no_eligible_profiles(self):
        # All SKIP → per_profile values all None → all_profiles_pass = False
        records = _make_records(500, agreement_class="SKIP_HONORED")
        report = self._agg(records)
        assert report.p4_gate_ok is False


# ---------------------------------------------------------------------------
# TestLoadWindow and _load_window
# ---------------------------------------------------------------------------

class TestLoadWindow:
    def test_empty_directory(self, tmp_path):
        result = list(_load_window(date(2026, 5, 1), date(2026, 5, 15), root=tmp_path))
        assert result == []

    def test_single_day_file_loaded(self, tmp_path):
        record = _make_record(agreement_class="AGREE")
        # Write a JSONL file for 2026-05-10
        from scripts.topology_v_next.divergence_logger import _serialize_record
        day = date(2026, 5, 10)
        path = tmp_path / f"divergence_{day.isoformat()}.jsonl"
        path.write_text(_serialize_record(record), encoding="utf-8")

        result = list(_load_window(day, day, root=tmp_path))
        assert len(result) == 1
        assert result[0].agreement_class == "AGREE"

    def test_date_range_filters_correctly(self, tmp_path):
        from scripts.topology_v_next.divergence_logger import _serialize_record
        record = _make_record(agreement_class="AGREE")

        for d in [date(2026, 5, 9), date(2026, 5, 10), date(2026, 5, 11)]:
            path = tmp_path / f"divergence_{d.isoformat()}.jsonl"
            path.write_text(_serialize_record(record), encoding="utf-8")

        # Only load [10, 10]
        result = list(_load_window(date(2026, 5, 10), date(2026, 5, 10), root=tmp_path))
        assert len(result) == 1

    def test_malformed_json_line_skipped(self, tmp_path, capsys):
        from scripts.topology_v_next.divergence_logger import _serialize_record
        good = _make_record(agreement_class="AGREE")
        day = date(2026, 5, 10)
        path = tmp_path / f"divergence_{day.isoformat()}.jsonl"
        path.write_text(
            "THIS IS NOT JSON\n" + _serialize_record(good),
            encoding="utf-8",
        )
        result = list(_load_window(day, day, root=tmp_path))
        assert len(result) == 1  # good record loaded
        assert result[0].agreement_class == "AGREE"
        captured = capsys.readouterr()
        assert "malformed record" in captured.err

    def test_multiple_records_per_file(self, tmp_path):
        from scripts.topology_v_next.divergence_logger import _serialize_record
        day = date(2026, 5, 10)
        path = tmp_path / f"divergence_{day.isoformat()}.jsonl"
        lines = "".join(
            _serialize_record(_make_record(agreement_class="AGREE")) for _ in range(5)
        )
        path.write_text(lines, encoding="utf-8")
        result = list(_load_window(day, day, root=tmp_path))
        assert len(result) == 5

    def test_load_window_public_wrapper(self, tmp_path):
        # load_window uses days_back; ensure it returns a list
        result = load_window(evidence_dir=tmp_path, days_back=7)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestWriteSummary
# ---------------------------------------------------------------------------

class TestWriteSummary:
    def _make_report(self, start="2026-05-01", end="2026-05-15") -> SummaryReport:
        return SummaryReport(
            date_range=(start, end),
            total_records=10,
            overall_agreement_pct_excluding_skips=0.9,
            per_profile_agreement={"p1": 0.9},
            skip_honored_rate=0.0,
            per_friction_pattern_count={fp.value: 0 for fp in FrictionPattern},
            sample_size_tier="marginal",
            p4_gate_ok=False,
        )

    def test_creates_file(self, tmp_path):
        report = self._make_report()
        write_summary(report, tmp_path)
        files = list(tmp_path.glob("divergence_summary_*.json"))
        assert len(files) == 1

    def test_filename_contains_dates(self, tmp_path):
        report = self._make_report("2026-05-01", "2026-05-15")
        write_summary(report, tmp_path)
        files = list(tmp_path.glob("divergence_summary_*.json"))
        assert "2026-05-01" in files[0].name
        assert "2026-05-15" in files[0].name

    def test_content_valid_json(self, tmp_path):
        report = self._make_report()
        write_summary(report, tmp_path)
        path = next(tmp_path.glob("divergence_summary_*.json"))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["total_records"] == 10
        assert "p4_gate_ok" in data

    def test_no_tmp_file_left_behind(self, tmp_path):
        report = self._make_report()
        write_summary(report, tmp_path)
        tmp_files = list(tmp_path.glob(".divergence_summary_*.tmp"))
        assert len(tmp_files) == 0

    def test_creates_output_dir_if_missing(self, tmp_path):
        report = self._make_report()
        nested = tmp_path / "a" / "b" / "c"
        write_summary(report, nested)
        files = list(nested.glob("divergence_summary_*.json"))
        assert len(files) == 1

    def test_write_summary_never_raises_on_bad_dir(self, tmp_path, capsys):
        # Pass an unwritable path; should not raise, just log to stderr
        report = self._make_report()
        # Create a file where a directory is expected
        blocker = tmp_path / "blocker"
        blocker.write_text("file")
        # write_summary should not raise even on OSError
        write_summary(report, blocker / "sub")  # blocker is a file, not a dir


# ---------------------------------------------------------------------------
# TestCLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_help_exits_cleanly(self):
        # cli_main catches SystemExit from argparse --help and returns its code
        result = cli_main(["--help"])
        assert result == 0

    def test_missing_required_args_returns_nonzero(self):
        result = cli_main([])
        assert result != 0

    def test_invalid_date_returns_2(self, tmp_path):
        result = cli_main([
            "--start-date", "not-a-date",
            "--end-date", "2026-05-15",
            "--root", str(tmp_path),
        ])
        assert result == 2

    def test_start_after_end_returns_2(self, tmp_path):
        result = cli_main([
            "--start-date", "2026-05-20",
            "--end-date", "2026-05-15",
            "--root", str(tmp_path),
        ])
        assert result == 2

    def test_empty_evidence_dir_returns_1(self, tmp_path):
        # 0 records → insufficient tier → exit 1
        result = cli_main([
            "--start-date", "2026-05-01",
            "--end-date", "2026-05-15",
            "--root", str(tmp_path),
        ])
        assert result == 1

    def test_returns_0_on_success(self, tmp_path):
        # Write 500 AGREE records to the evidence dir so tier = sufficient
        from scripts.topology_v_next.divergence_logger import _serialize_record
        day = date(2026, 5, 10)
        path = tmp_path / f"divergence_{day.isoformat()}.jsonl"
        lines = "".join(
            _serialize_record(_make_record(agreement_class="AGREE")) for _ in range(500)
        )
        path.write_text(lines, encoding="utf-8")

        result = cli_main([
            "--start-date", "2026-05-10",
            "--end-date", "2026-05-10",
            "--root", str(tmp_path),
        ])
        assert result == 0

    def test_out_flag_writes_summary_file(self, tmp_path):
        from scripts.topology_v_next.divergence_logger import _serialize_record
        evidence_dir = tmp_path / "evidence"
        evidence_dir.mkdir()
        out_dir = tmp_path / "out"

        day = date(2026, 5, 10)
        path = evidence_dir / f"divergence_{day.isoformat()}.jsonl"
        lines = "".join(
            _serialize_record(_make_record(agreement_class="AGREE")) for _ in range(5)
        )
        path.write_text(lines, encoding="utf-8")

        cli_main([
            "--start-date", "2026-05-10",
            "--end-date", "2026-05-10",
            "--root", str(evidence_dir),
            "--out", str(out_dir),
        ])
        files = list(out_dir.glob("divergence_summary_*.json"))
        assert len(files) == 1

    def test_include_skip_honored_flag_accepted(self, tmp_path):
        # Ensure --include-skip-honored is accepted without error
        result = cli_main([
            "--start-date", "2026-05-01",
            "--end-date", "2026-05-15",
            "--root", str(tmp_path),
            "--include-skip-honored",
        ])
        # 0 records → exit 1 (not 2)
        assert result == 1


# ---------------------------------------------------------------------------
# TestIntegration
# ---------------------------------------------------------------------------

class TestIntegration:
    """
    End-to-end: write records via P3.1 log_divergence, read and aggregate via P3.2.
    """

    def test_log_then_aggregate_roundtrip(self, tmp_path):
        evidence_root = tmp_path / "evidence"

        # Write 10 records using P3.1 log_divergence
        for i in range(10):
            record = _make_record(
                agreement_class="AGREE" if i < 8 else "DISAGREE_SEVERITY",
                profile_resolved_new="integration_profile",
            )
            log_divergence(record, root=evidence_root)

        today = datetime.now(UTC).date()
        result = aggregate(
            today,
            today,
            root=evidence_root,
        )

        assert result["total_records"] == 10
        assert result["overall_agreement_pct_excluding_skips"] == pytest.approx(8 / 10)
        assert "integration_profile" in result["per_profile_agreement"]
        assert result["per_profile_agreement"]["integration_profile"] == pytest.approx(0.8)

    def test_log_sufficient_records_p4_evaluation(self, tmp_path):
        evidence_root = tmp_path / "evidence"

        # Write 500 AGREE records
        for _ in range(500):
            record = _make_record(
                agreement_class="AGREE",
                profile_resolved_new="p1",
            )
            log_divergence(record, root=evidence_root)

        today = datetime.now(UTC).date()
        result = aggregate(today, today, root=evidence_root)

        assert result["sample_size_tier"] == "sufficient"
        assert result["p4_gate_ok"] is True

    def test_write_summary_in_aggregate(self, tmp_path):
        evidence_root = tmp_path / "evidence"
        out_dir = tmp_path / "summaries"

        today = datetime.now(UTC).date()
        aggregate(today, today, root=evidence_root, out_path=out_dir)

        files = list(out_dir.glob("divergence_summary_*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert "p4_gate_ok" in data
        assert "total_records" in data
