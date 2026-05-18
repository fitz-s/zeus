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

# Pattern: f-string table aliasing that would bypass the literal-SQL antibody.
# _ensemble_snapshots_table() returned a legacy table name; its presence in src/
# means dual-write infrastructure survived the v1.F20 removal.
_FSTRING_PATTERN = r"_ensemble_snapshots_table("


def _run_grep(args: list[str], cwd: Path) -> list[str]:
    """Run grep and return stdout lines. Validates returncode in (0, 1).

    grep exit codes: 0 = matches found, 1 = no matches, 2+ = error
    (bad path, permission denied, etc.). Silently treating exit 2 as
    "no matches" would make the antibody a false-green on broken paths.
    """
    result = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    assert result.returncode in (0, 1), (
        f"grep exited with code {result.returncode} (bad path or permission error).\n"
        f"stderr: {result.stderr.strip()!r}\n"
        f"command: {args}"
    )
    return result.stdout.splitlines()


def _find_legacy_readers() -> list[str]:
    """Return list of 'file:line:content' strings matching the legacy table."""
    lines = _run_grep(
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
        cwd=_REPO_ROOT,
    )
    hits = []
    for line in lines:
        # Exclude lines that reference ensemble_snapshots_v2
        if _V2_SUFFIX not in line.split(":", 2)[-1]:
            hits.append(line)
    return hits


def _find_fstring_legacy_aliases() -> list[str]:
    """Find f-string table aliasing that would bypass the literal-SQL probe.

    _ensemble_snapshots_table() was the legacy resolver; any call site surviving
    v1.F20 in src/ means the dual-write infrastructure was not fully removed.
    """
    return _run_grep(
        [
            "grep", "-rn", "--include=*.py",
            # Only scan src/ — tests/ may reference the symbol in skip-annotated
            # retirement stubs (e.g. test_legacy_snapshot_projection_upsert.py).
            # The definition site in evaluator.py itself is the only src/ hit
            # that would survive a partial removal, so no exclude needed here.
            _FSTRING_PATTERN,
            "src/",
        ],
        cwd=_REPO_ROOT,
    )


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


def test_no_src_uses_fstring_legacy_table_alias():
    """No file in src/ may call _ensemble_snapshots_table() (v1.F20 removed).

    This catches f-string dual-write patterns that bypass the literal-SQL probe:
      legacy_table = _ensemble_snapshots_table(conn)
      conn.execute(f\"INSERT INTO {legacy_table} ...\")
    These patterns are NOT caught by FROM ensemble_snapshots grep.
    """
    hits = _find_fstring_legacy_aliases()
    assert hits == [], (
        "Legacy _ensemble_snapshots_table() call(s) found in src/ after v1.F20.\n"
        "The function and all call sites must be removed:\n"
        + "\n".join(f"  {h}" for h in hits)
    )
