# Created: 2026-05-18
# Last reused or audited: 2026-05-28
# Authority basis: v1.F20 reader migration + B3 table rename (ensemble_snapshots_v2
#   → ensemble_snapshots). After B3, ensemble_snapshots IS the canonical name.
"""Antibody: no src/ or tests/ file may query FROM ensemble_snapshots_v2 (retired suffix).

After B3 rename (PR3), ensemble_snapshots is the canonical table name.
This antibody guards that no stale _v2 suffix slips back in.

Sed-break verification (run manually to confirm antibody fires):
  1. Inject a fake reader:
       echo "# FROM ensemble_snapshots_v2 WHERE 1=0" >> src/engine/evaluator.py
  2. Run this test — expect FAIL.
  3. Restore:
       git checkout src/engine/evaluator.py
  4. Re-run — expect PASS.
"""
import subprocess
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent

# Post-B3: ensemble_snapshots_v2 is the retired name; ensemble_snapshots is canonical.
_RETIRED_TABLE_PATTERN = r"ensemble_snapshots_v2"

# Post-B3: _ensemble_snapshots_v2_table() was the old dynamic resolver; it must
# not appear in src/ (was renamed to _ensemble_snapshots_table() by B3).
_RETIRED_FSTRING_PATTERN = r"_ensemble_snapshots_v2_table("


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


def _find_retired_table_refs() -> list[str]:
    """Return file:line:content strings matching the retired _v2 table name."""
    return _run_grep(
        [
            "grep", "-rn", "--include=*.py",
            "--exclude=test_no_legacy_ensemble_snapshots_reader.py",
            _RETIRED_TABLE_PATTERN,
            "src/",
            "tests/",
        ],
        cwd=_REPO_ROOT,
    )


def _find_retired_fstring_aliases() -> list[str]:
    """Find references to the retired _v2 dynamic resolver in src/."""
    return _run_grep(
        [
            "grep", "-rn", "--include=*.py",
            _RETIRED_FSTRING_PATTERN,
            "src/",
        ],
        cwd=_REPO_ROOT,
    )


def test_no_src_or_tests_reads_legacy_ensemble_snapshots():
    """No file in src/ or tests/ may contain 'ensemble_snapshots_v2' (retired).

    After B3 rename, ensemble_snapshots is canonical. _v2 suffix is forbidden.
    """
    hits = _find_retired_table_refs()
    assert hits == [], (
        "Retired ensemble_snapshots_v2 reference(s) found after B3 rename.\n"
        "Each line must be updated to use the canonical 'ensemble_snapshots':\n"
        + "\n".join(f"  {h}" for h in hits)
    )


def test_no_src_uses_fstring_legacy_table_alias():
    """No file in src/ may call _ensemble_snapshots_v2_table() (retired by B3).

    B3 renamed _ensemble_snapshots_v2_table() → _ensemble_snapshots_table().
    The old name must not reappear.
    """
    hits = _find_retired_fstring_aliases()
    assert hits == [], (
        "Retired _ensemble_snapshots_v2_table() call(s) found in src/ after B3.\n"
        "The function was renamed to _ensemble_snapshots_table() by B3:\n"
        + "\n".join(f"  {h}" for h in hits)
    )
