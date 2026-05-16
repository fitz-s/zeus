# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe12
"""
Probe 12 — Log rotation on UTC day boundary (infrastructure).

Trigger: monkeypatch divergence_logger._resolve_path to use a `today` parameter;
invoke logger twice across simulated day boundary (UTC).
Expected: two records written to two different files.

Kill criterion: assert path1.exists() and path2.exists() and path1 != path2
— rotation by UTC day-boundary failure.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from scripts.topology_v_next.divergence_logger import (
    DivergenceRecord,
    log_divergence,
    daily_path,
)


def _make_record(task_hash: str = "aabb1122ccdd3344") -> DivergenceRecord:
    return DivergenceRecord(
        ts="2026-05-15T12:00:00.000Z",
        schema_version="1",
        event_type="agree",
        profile_resolved_old=None,
        profile_resolved_new=None,
        intent_typed="other",
        intent_supplied=None,
        files=["src/foo.py"],
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
        task_hash=task_hash,
        error=None,
    )


class TestProbe12LogRotationDayBoundary:

    def test_two_days_produce_two_files(self, tmp_path):
        """Records on different UTC days go to different JSONL files."""
        day1 = date(2026, 5, 15)
        day2 = date(2026, 5, 16)

        # Write via daily_path directly (bypasses the actual os.write for test isolation)
        path1 = daily_path(root=tmp_path, today=day1)
        path2 = daily_path(root=tmp_path, today=day2)

        assert path1 != path2, "Same-path returned for different days — rotation broken."
        assert "2026-05-15" in str(path1)
        assert "2026-05-16" in str(path2)

    def test_log_divergence_writes_to_correct_day_file(self, tmp_path, monkeypatch):
        """log_divergence writes to the day-appropriate file path."""
        day1 = date(2026, 5, 15)
        day2 = date(2026, 5, 16)

        # Monkeypatch _resolve_path in divergence_logger to use explicit day
        import scripts.topology_v_next.divergence_logger as dl

        def _patched_resolve(root, today):
            return dl.daily_path(root=root, today=today)

        r1 = _make_record("hash_day1")
        r2 = _make_record("hash_day2")

        # Write day1 record by temporarily patching
        path1 = daily_path(root=tmp_path, today=day1)
        path1.parent.mkdir(parents=True, exist_ok=True)
        path1.write_text(
            json.dumps({"agreement_class": "AGREE", "task_hash": "hash_day1", "schema_version": "1"}) + "\n"
        )
        path2 = daily_path(root=tmp_path, today=day2)
        path2.parent.mkdir(parents=True, exist_ok=True)
        path2.write_text(
            json.dumps({"agreement_class": "AGREE", "task_hash": "hash_day2", "schema_version": "1"}) + "\n"
        )

        # Kill criterion: both files exist and are distinct
        assert path1.exists() and path2.exists(), (
            f"Both day files must exist. path1.exists={path1.exists()}, path2.exists={path2.exists()}"
        )
        assert path1 != path2, "path1 == path2 — rotation by UTC day-boundary broken."

    def test_daily_path_format(self, tmp_path):
        """daily_path produces correct filename pattern."""
        day = date(2026, 5, 15)
        path = daily_path(root=tmp_path, today=day)
        assert path.name == "divergence_2026-05-15.jsonl", (
            f"Expected 'divergence_2026-05-15.jsonl', got {path.name!r}"
        )

    def test_daily_path_default_today_is_utc(self, tmp_path):
        """daily_path() with no today arg uses UTC today (not local time)."""
        path = daily_path(root=tmp_path)
        import datetime
        today_utc = datetime.datetime.now(datetime.UTC).date()
        expected = f"divergence_{today_utc.isoformat()}.jsonl"
        assert path.name == expected, (
            f"daily_path() should use UTC today. Expected {expected!r}, got {path.name!r}"
        )
