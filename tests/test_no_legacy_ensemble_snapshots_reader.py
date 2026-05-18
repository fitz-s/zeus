# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: v1.F20 reader migration — ensemble_snapshots readers removed;
#   only ensemble_snapshots_v2 may appear in src/ and tests/ SQL.
"""Antibody: no src/ or tests/ file may query FROM ensemble_snapshots (legacy).

Sed-break verification (run manually to confirm antibody fires):
  1. Inject a fake reader:
       echo "# FROM ensemble_snapshots WHERE 1=0" >> src/engine/evaluator.py
  2. Run this test — expect FAIL.
  3. Restore:
       git checkout src/engine/evaluator.py
  4. Re-run — expect PASS.
"""
import re
import subprocess
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent

# Pattern: FROM ensemble_snapshots NOT followed by _v2
# Uses negative lookahead via post-processing (grep -v _v2 after initial match).
_GREP_PATTERN = r"FROM ensemble_snapshots"
_V2_SUFFIX = "_v2"


def _find_legacy_readers() -> list[str]:
    """Return list of 'file:line:content' strings matching the legacy table."""
    result = subprocess.run(
        [
            "grep", "-rn", "--include=*.py",
            # Exclude the antibody file itself (self-referential) and retired/skipped
            # test files that still contain legacy SQL in skipped test bodies.
            "--exclude=test_no_legacy_ensemble_snapshots_reader.py",
            "--exclude=test_legacy_snapshot_projection_upsert.py",
            "--exclude=test_ensemble_snapshots_bias_corrected_schema.py",
            "--exclude=test_tigge_snapshot_p_raw_backfill.py",
            _GREP_PATTERN,
            "src/",
            "tests/",
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    hits = []
    for line in result.stdout.splitlines():
        # Exclude lines that reference ensemble_snapshots_v2
        if _V2_SUFFIX not in line.split(":", 2)[-1]:
            hits.append(line)
    return hits


def test_no_src_or_tests_reads_legacy_ensemble_snapshots():
    """No file in src/ or tests/ may contain 'FROM ensemble_snapshots' (legacy).

    ensemble_snapshots_v2 references are allowed; bare ensemble_snapshots are not.
    This is the v1.F20 reader-migration antibody.
    """
    hits = _find_legacy_readers()
    assert hits == [], (
        "Legacy ensemble_snapshots reader(s) found after v1.F20 migration.\n"
        "Each line below must be migrated to ensemble_snapshots_v2:\n"
        + "\n".join(f"  {h}" for h in hits)
    )
