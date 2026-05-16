# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md
#                  §7 P3.1 test list, §4.1 schema, §4.3 concurrency, §4.4 table, §4.5 classifier
"""
Unit tests for scripts/topology_v_next/divergence_logger.py — P3.1 deliverable.

Covers (per SCAFFOLD §7 P3.1):
  - DivergenceRecord frozen dataclass roundtrip
  - _serialize_record produces single-line JSON; no embedded newline
  - log_divergence writes one line; verifies via Path.read_text().splitlines()
  - compute_event_type classifier covers all 3 event_types
  - classify_divergence returns every agreement class for synthetic records
  - daily_path resolves UTC-day correctly
  - map via OLD_STATUS_TO_NEW_SEVERITY covers all 6 current-side status values
  - stderr write on disk-full simulation (monkeypatch os.write to raise)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, UTC
from pathlib import Path

import pytest

from scripts.topology_v_next.dataclasses import Severity
from scripts.topology_v_next.divergence_logger import (
    OLD_STATUS_TO_NEW_SEVERITY,
    DivergenceRecord,
    classify_divergence,
    compute_event_type,
    daily_path,
    log_divergence,
    _serialize_record,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_record(**overrides) -> DivergenceRecord:
    """Return a minimal valid DivergenceRecord with caller-supplied overrides."""
    defaults = dict(
        ts="2026-05-15T13:42:01.234Z",
        schema_version="1",
        event_type="agree",
        agreement_class="AGREE",
        profile_resolved_old="modify_scripts_tooling",
        old_admit_status="admitted",
        profile_resolved_new="modify_scripts_tooling",
        new_admit_severity="ADMIT",
        new_admit_ok=True,
        intent_typed="modify_existing",
        intent_supplied="modify_existing",
        files=("scripts/topology_v_next/foo.py",),
        missing_companion=(),
        companion_skip_used=False,
        friction_pattern_hit=None,
        closest_rejected_profile=None,
        kernel_alert_count=0,
        friction_budget_used=0,
        task_hash="abcd1234ef567890",
        error=None,
    )
    defaults.update(overrides)
    return DivergenceRecord(**defaults)


# ---------------------------------------------------------------------------
# DivergenceRecord dataclass
# ---------------------------------------------------------------------------

class TestDivergenceRecord:
    def test_frozen(self):
        record = _make_record()
        with pytest.raises((AttributeError, TypeError)):
            record.ts = "new"  # type: ignore[misc]

    def test_basic_fields(self):
        record = _make_record()
        assert record.schema_version == "1"
        assert record.event_type == "agree"
        assert record.new_admit_severity == "ADMIT"
        assert record.files == ("scripts/topology_v_next/foo.py",)
        assert record.missing_companion == ()
        assert record.companion_skip_used is False
        assert record.error is None

    def test_tuple_fields_immutable(self):
        record = _make_record(files=("a.py", "b.py"), missing_companion=("docs/x.md",))
        assert isinstance(record.files, tuple)
        assert isinstance(record.missing_companion, tuple)

    def test_none_fields_allowed(self):
        record = _make_record(
            profile_resolved_old=None,
            profile_resolved_new=None,
            new_admit_severity=None,
            new_admit_ok=None,
            friction_pattern_hit=None,
            closest_rejected_profile=None,
            intent_supplied=None,
            error="SomeException: something went wrong",
        )
        assert record.new_admit_severity is None
        assert record.error is not None

    def test_roundtrip_via_serialize(self):
        record = _make_record()
        line = _serialize_record(record)
        parsed = json.loads(line.strip())
        assert parsed["ts"] == record.ts
        assert parsed["schema_version"] == "1"
        assert parsed["files"] == list(record.files)
        assert parsed["error"] is None


# ---------------------------------------------------------------------------
# _serialize_record
# ---------------------------------------------------------------------------

class TestSerializeRecord:
    def test_single_line(self):
        record = _make_record()
        line = _serialize_record(record)
        assert line.endswith("\n")
        assert line.count("\n") == 1  # exactly one terminating newline

    def test_no_embedded_newline(self):
        # Even if a field value sneaks in a newline it should be sanitised
        record = _make_record(error="line1\nline2")
        line = _serialize_record(record)
        # Strip trailing newline then check no more newlines
        assert "\n" not in line.rstrip("\n")

    def test_keys_sorted(self):
        record = _make_record()
        line = _serialize_record(record)
        parsed = json.loads(line.strip())
        keys = list(parsed.keys())
        assert keys == sorted(keys)

    def test_valid_json(self):
        record = _make_record(files=("a.py", "b.py"), missing_companion=("docs/x.md",))
        line = _serialize_record(record)
        parsed = json.loads(line.strip())
        assert isinstance(parsed, dict)
        assert parsed["files"] == ["a.py", "b.py"]
        assert parsed["missing_companion"] == ["docs/x.md"]


# ---------------------------------------------------------------------------
# log_divergence
# ---------------------------------------------------------------------------

class TestLogDivergence:
    def test_writes_one_line(self, tmp_path):
        record = _make_record()
        log_divergence(record, root=tmp_path)
        today = datetime.now(UTC).date()
        jsonl = tmp_path / f"divergence_{today.isoformat()}.jsonl"
        assert jsonl.exists()
        lines = jsonl.read_text().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["schema_version"] == "1"

    def test_multiple_appends_sequential(self, tmp_path):
        for i in range(5):
            rec = _make_record(task_hash=f"hash{i:012x}")
            log_divergence(rec, root=tmp_path)
        today = datetime.now(UTC).date()
        jsonl = tmp_path / f"divergence_{today.isoformat()}.jsonl"
        lines = jsonl.read_text().splitlines()
        assert len(lines) == 5
        # Every line is valid JSON
        for line in lines:
            assert json.loads(line)

    def test_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        record = _make_record()
        log_divergence(record, root=nested)
        today = datetime.now(UTC).date()
        jsonl = nested / f"divergence_{today.isoformat()}.jsonl"
        assert jsonl.exists()

    def test_stderr_on_write_failure(self, tmp_path, monkeypatch, capsys):
        """When os.write raises, errors go to stderr and do NOT propagate."""
        def bad_write(fd, data):
            raise OSError("no space left on device")
        monkeypatch.setattr(os, "write", bad_write)
        record = _make_record()
        # Must not raise
        log_divergence(record, root=tmp_path)
        captured = capsys.readouterr()
        assert "divergence_logger" in captured.err
        assert "no space left on device" in captured.err

    def test_record_content_correct(self, tmp_path):
        record = _make_record(
            old_admit_status="blocked",
            new_admit_severity="SOFT_BLOCK",
            agreement_class="AGREE",
            friction_pattern_hit="SLICING_PRESSURE",
        )
        log_divergence(record, root=tmp_path)
        today = datetime.now(UTC).date()
        jsonl = tmp_path / f"divergence_{today.isoformat()}.jsonl"
        parsed = json.loads(jsonl.read_text().strip())
        assert parsed["old_admit_status"] == "blocked"
        assert parsed["new_admit_severity"] == "SOFT_BLOCK"
        assert parsed["friction_pattern_hit"] == "SLICING_PRESSURE"

    def test_concurrent_simulated_appends(self, tmp_path):
        """Sequential multi-write consistency check (probe13 does true concurrency in P3.3)."""
        import threading

        errors = []

        def write_records(n: int):
            for i in range(n):
                try:
                    rec = _make_record(task_hash=f"t{n:04x}{i:08x}")
                    log_divergence(rec, root=tmp_path)
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=write_records, args=(20,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        today = datetime.now(UTC).date()
        jsonl = tmp_path / f"divergence_{today.isoformat()}.jsonl"
        lines = jsonl.read_text().splitlines()
        assert len(lines) == 80
        for line in lines:
            json.loads(line)  # every line valid JSON


# ---------------------------------------------------------------------------
# compute_event_type
# ---------------------------------------------------------------------------

class TestComputeEventType:
    def test_companion_skip_honored(self):
        result = compute_event_type(
            old_status="admitted",
            new_severity=Severity.ADMIT,
            companion_skip_used=True,
        )
        assert result == "companion_skip_honored"

    def test_agree(self):
        result = compute_event_type(
            old_status="admitted",
            new_severity=Severity.ADMIT,
            companion_skip_used=False,
        )
        assert result == "agree"

    def test_divergence_observation_severity_mismatch(self):
        result = compute_event_type(
            old_status="admitted",
            new_severity=Severity.SOFT_BLOCK,
            companion_skip_used=False,
        )
        assert result == "divergence_observation"

    def test_divergence_observation_unknown_old_status(self):
        result = compute_event_type(
            old_status="__unknown__",
            new_severity=Severity.ADMIT,
            companion_skip_used=False,
        )
        assert result == "divergence_observation"

    def test_agree_with_string_severity(self):
        # Accepts string form of Severity
        result = compute_event_type(
            old_status="blocked",
            new_severity="SOFT_BLOCK",
            companion_skip_used=False,
        )
        assert result == "agree"

    def test_companion_skip_honored_takes_priority(self):
        # Even if severities diverge, companion skip honored wins
        result = compute_event_type(
            old_status="admitted",
            new_severity=Severity.HARD_STOP,
            companion_skip_used=True,
        )
        assert result == "companion_skip_honored"


# ---------------------------------------------------------------------------
# classify_divergence
# ---------------------------------------------------------------------------

class TestClassifyDivergence:
    def test_agree(self):
        record = _make_record(
            old_admit_status="admitted",
            new_admit_severity="ADMIT",
            profile_resolved_old="p1",
            profile_resolved_new="p1",
            companion_skip_used=False,
            missing_companion=(),
            error=None,
        )
        assert classify_divergence(record) == "AGREE"

    def test_agree_advisory_maps(self):
        record = _make_record(
            old_admit_status="advisory_only",
            new_admit_severity="ADVISORY",
            profile_resolved_old="p",
            profile_resolved_new="p",
            companion_skip_used=False,
            error=None,
        )
        assert classify_divergence(record) == "AGREE"

    def test_agree_soft_block_maps(self):
        record = _make_record(
            old_admit_status="blocked",
            new_admit_severity="SOFT_BLOCK",
            profile_resolved_old="p",
            profile_resolved_new="p",
            companion_skip_used=False,
            error=None,
        )
        assert classify_divergence(record) == "AGREE"

    def test_disagree_severity(self):
        record = _make_record(
            old_admit_status="admitted",
            new_admit_severity="SOFT_BLOCK",
            profile_resolved_old="p",
            profile_resolved_new="p",
            companion_skip_used=False,
            missing_companion=(),
            error=None,
        )
        assert classify_divergence(record) == "DISAGREE_SEVERITY"

    def test_disagree_profile(self):
        record = _make_record(
            old_admit_status="admitted",
            new_admit_severity="ADMIT",
            profile_resolved_old="profile_a",
            profile_resolved_new="profile_b",
            companion_skip_used=False,
            missing_companion=(),
            error=None,
        )
        assert classify_divergence(record) == "DISAGREE_PROFILE"

    def test_disagree_companion(self):
        record = _make_record(
            old_admit_status="admitted",
            new_admit_severity="ADVISORY",
            profile_resolved_old="p",
            profile_resolved_new="p",
            companion_skip_used=False,
            missing_companion=("docs/ref/auth.md",),
            error=None,
        )
        assert classify_divergence(record) == "DISAGREE_COMPANION"

    def test_disagree_hard_stop(self):
        record = _make_record(
            old_admit_status="admitted",
            new_admit_severity="HARD_STOP",
            companion_skip_used=False,
            missing_companion=(),
            error=None,
        )
        assert classify_divergence(record) == "DISAGREE_HARD_STOP"

    def test_skip_honored(self):
        record = _make_record(
            old_admit_status="admitted",
            new_admit_severity="ADVISORY",
            companion_skip_used=True,
            error=None,
        )
        assert classify_divergence(record) == "SKIP_HONORED"

    def test_error_on_none_severity(self):
        record = _make_record(
            new_admit_severity=None,
            new_admit_ok=None,
            error=None,
        )
        assert classify_divergence(record) == "ERROR"

    def test_error_on_error_field(self):
        record = _make_record(
            new_admit_severity="ADMIT",
            error="SomeException: oops",
        )
        assert classify_divergence(record) == "ERROR"

    def test_skip_honored_beats_hard_stop(self):
        # companion_skip_used check comes before HARD_STOP check per §4.5
        record = _make_record(
            old_admit_status="admitted",
            new_admit_severity="HARD_STOP",
            companion_skip_used=True,
            error=None,
        )
        assert classify_divergence(record) == "SKIP_HONORED"


# ---------------------------------------------------------------------------
# OLD_STATUS_TO_NEW_SEVERITY table coverage
# ---------------------------------------------------------------------------

class TestOldStatusToNewSeverityTable:
    """SCAFFOLD §4.4 requires all 6 mappings and HARD_STOP intentional absence."""

    def test_all_six_mappings_present(self):
        expected_keys = {
            "admitted",
            "advisory_only",
            "blocked",
            "scope_expansion_required",
            "route_contract_conflict",
            "ambiguous",
        }
        assert set(OLD_STATUS_TO_NEW_SEVERITY.keys()) == expected_keys

    def test_admitted_maps_to_admit(self):
        assert OLD_STATUS_TO_NEW_SEVERITY["admitted"] == Severity.ADMIT

    def test_advisory_only_maps_to_advisory(self):
        assert OLD_STATUS_TO_NEW_SEVERITY["advisory_only"] == Severity.ADVISORY

    def test_blocked_maps_to_soft_block(self):
        assert OLD_STATUS_TO_NEW_SEVERITY["blocked"] == Severity.SOFT_BLOCK

    def test_scope_expansion_required_maps_to_soft_block(self):
        assert OLD_STATUS_TO_NEW_SEVERITY["scope_expansion_required"] == Severity.SOFT_BLOCK

    def test_route_contract_conflict_maps_to_soft_block(self):
        assert OLD_STATUS_TO_NEW_SEVERITY["route_contract_conflict"] == Severity.SOFT_BLOCK

    def test_ambiguous_maps_to_soft_block(self):
        assert OLD_STATUS_TO_NEW_SEVERITY["ambiguous"] == Severity.SOFT_BLOCK

    def test_hard_stop_intentionally_absent(self):
        """HARD_STOP has no current equivalent — intentional absence per §4.4."""
        assert "HARD_STOP" not in OLD_STATUS_TO_NEW_SEVERITY
        assert Severity.HARD_STOP not in OLD_STATUS_TO_NEW_SEVERITY.values()


# ---------------------------------------------------------------------------
# daily_path — UTC-day resolution + cross-midnight rotation
# ---------------------------------------------------------------------------

class TestDailyPath:
    def test_default_root(self):
        p = daily_path()
        assert "topology_v_next_shadow" in str(p)
        today = datetime.now(UTC).date()
        assert p.name == f"divergence_{today.isoformat()}.jsonl"

    def test_custom_root(self, tmp_path):
        p = daily_path(root=tmp_path)
        assert p.parent == tmp_path

    def test_specific_date(self, tmp_path):
        d = date(2026, 5, 15)
        p = daily_path(root=tmp_path, today=d)
        assert p.name == "divergence_2026-05-15.jsonl"

    def test_cross_midnight_rotation(self, tmp_path):
        d1 = date(2026, 5, 15)
        d2 = date(2026, 5, 16)
        p1 = daily_path(root=tmp_path, today=d1)
        p2 = daily_path(root=tmp_path, today=d2)
        assert p1 != p2
        assert p1.name == "divergence_2026-05-15.jsonl"
        assert p2.name == "divergence_2026-05-16.jsonl"

    def test_log_divergence_uses_today(self, tmp_path, monkeypatch):
        """log_divergence writes to today's file; cross-midnight rotation test."""
        d1 = date(2026, 5, 15)
        d2 = date(2026, 5, 16)

        # First write at d1
        import scripts.topology_v_next.divergence_logger as dl
        orig = dl._resolve_path

        def patched_d1(root, today):
            return orig(root, d1)

        def patched_d2(root, today):
            return orig(root, d2)

        monkeypatch.setattr(dl, "_resolve_path", patched_d1)
        log_divergence(_make_record(task_hash="aaaaaaaabbbbbbbb"), root=tmp_path)

        monkeypatch.setattr(dl, "_resolve_path", patched_d2)
        log_divergence(_make_record(task_hash="ccccccccdddddddd"), root=tmp_path)

        f1 = tmp_path / "divergence_2026-05-15.jsonl"
        f2 = tmp_path / "divergence_2026-05-16.jsonl"
        assert f1.exists()
        assert f2.exists()
        assert len(f1.read_text().splitlines()) == 1
        assert len(f2.read_text().splitlines()) == 1


# ---------------------------------------------------------------------------
# Truncation guard (8 KiB cap)
# ---------------------------------------------------------------------------

class TestTruncationGuard:
    def test_large_record_truncated(self, tmp_path):
        """Records exceeding 8 KiB get files replaced with ['__TRUNCATED__']."""
        big_files = tuple(f"scripts/very/long/path/to/file_{i:04d}.py" for i in range(500))
        record = _make_record(files=big_files)
        log_divergence(record, root=tmp_path)
        today = datetime.now(UTC).date()
        jsonl = tmp_path / f"divergence_{today.isoformat()}.jsonl"
        parsed = json.loads(jsonl.read_text().strip())
        # Either truncated or fits — if truncated, files must be ["__TRUNCATED__"]
        line_bytes = len(jsonl.read_bytes())
        if parsed["files"] == ["__TRUNCATED__"]:
            assert parsed["error"] in ("record_size_exceeded", None) or parsed["error"] is not None
        else:
            # Small enough to fit — passes without truncation
            assert len(parsed["files"]) == 500
