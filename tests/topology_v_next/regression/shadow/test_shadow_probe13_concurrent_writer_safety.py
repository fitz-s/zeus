# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe13
"""
Probe 13 — Concurrent writer safety (infrastructure).

Trigger: spawn 4 subprocesses each writing 100 records to the same daily file
via divergence_logger.log_divergence.
Expected: file contains exactly 400 lines; every line is valid JSON; no line corrupted.

Kill criterion: assert line_count == 400 and all(json.loads(line) for line in lines)
— interleaving means O_APPEND atomicity is broken.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scripts.topology_v_next.divergence_logger import daily_path


WRITER_SCRIPT = textwrap.dedent("""
    import sys, json
    from datetime import date
    from pathlib import Path
    from scripts.topology_v_next.divergence_logger import DivergenceRecord, log_divergence

    root = Path(sys.argv[1])
    today = date.fromisoformat(sys.argv[2])
    worker_id = sys.argv[3]

    for i in range(100):
        record = DivergenceRecord(
            ts=f"2026-05-15T12:00:{i:02d}.000Z",
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
            task_hash=f"{worker_id}_{i:04d}_aabbccdd",
            error=None,
        )
        log_divergence(record, root=root)
""")


class TestProbe13ConcurrentWriterSafety:

    def test_400_lines_no_corruption(self, tmp_path):
        """4 concurrent workers writing 100 records each produce 400 valid JSONL lines."""
        import datetime
        today = datetime.datetime.now(datetime.UTC).date()
        today_str = today.isoformat()

        # Write the worker script to a temp file
        script_file = tmp_path / "writer.py"
        script_file.write_text(WRITER_SCRIPT)

        # Inherit PYTHONPATH so the subprocess can find scripts.topology_v_next
        env = os.environ.copy()
        repo_root = str(Path(__file__).resolve().parents[4])
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{repo_root}:{existing_pp}" if existing_pp else repo_root

        processes = []
        for i in range(4):
            p = subprocess.Popen(
                [sys.executable, str(script_file), str(tmp_path), today_str, f"worker{i}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=repo_root,
            )
            processes.append(p)

        # Wait for all to finish
        for p in processes:
            stdout, stderr = p.communicate(timeout=30)
            assert p.returncode == 0, (
                f"Worker process failed with returncode {p.returncode}.\n"
                f"stderr: {stderr.decode()!r}"
            )

        log_file = daily_path(root=tmp_path, today=today)
        assert log_file.exists(), f"Log file was not created: {log_file}"

        lines = log_file.read_text().splitlines()

        # Kill criterion 1: exactly 400 lines
        assert len(lines) == 400, (
            f"Expected 400 JSONL lines from 4 × 100 writes. Got {len(lines)}. "
            f"O_APPEND atomicity may be broken (interleaved partial writes)."
        )

        # Kill criterion 2: every line is valid JSON
        malformed = []
        for i, line in enumerate(lines):
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                malformed.append((i, line[:80], str(e)))

        assert not malformed, (
            f"Malformed JSONL lines detected ({len(malformed)} of 400). "
            f"First malformed: {malformed[0]}. "
            f"O_APPEND atomicity broken — concurrent writes interleaved."
        )

    def test_single_writer_produces_correct_line_count(self, tmp_path):
        """Sanity: single writer produces exactly 100 lines."""
        import datetime
        today = datetime.datetime.now(datetime.UTC).date()
        today_str = today.isoformat()

        script_file = tmp_path / "writer.py"
        script_file.write_text(WRITER_SCRIPT)

        env = os.environ.copy()
        repo_root = str(Path(__file__).resolve().parents[4])
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{repo_root}:{existing_pp}" if existing_pp else repo_root

        result = subprocess.run(
            [sys.executable, str(script_file), str(tmp_path), today_str, "single"],
            capture_output=True,
            timeout=30,
            env=env,
            cwd=repo_root,
        )
        assert result.returncode == 0, f"Single writer failed: {result.stderr.decode()!r}"

        log_file = daily_path(root=tmp_path, today=today)
        lines = log_file.read_text().splitlines()
        assert len(lines) == 100, f"Expected 100 lines, got {len(lines)}"
