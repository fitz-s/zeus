# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: CI_PR_159_CRITIC.md P2/M4; CI_IMPROVEMENT_DESIGN.md; PR #159 regex fix

"""Self-defense invariants for .github/workflows/full-pytest-sweep.yml.

Guards against future agents silently narrowing the sweep scope (e.g. by
adding --ignore=, -k, --deselect, or -m flags to the pytest command line).
Also ensures the 8-category taxonomy sentinel comment block remains intact.
"""

import yaml
from pathlib import Path

WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "full-pytest-sweep.yml"

FORBIDDEN_PYTEST_FLAGS = ["--ignore=", " -k ", "--deselect", " -m "]
REQUIRED_TAXONOMY_SNIPPETS = [
    "Schema/registry drift",
    "Signature drift across module boundary",
    "Symbol rename",
    "Allowlist/boundary contract drift",
    "Enum/literal completeness drift",
    "Static-count assertions",
    "Path-filtered linter leak",
    "Settlement/state-machine drift",
]


def _load_workflow() -> dict:
    with WORKFLOW_PATH.open() as f:
        return yaml.safe_load(f)


def _pytest_run_command(workflow: dict) -> str:
    """Return the run: block from the 'Run full pytest sweep' step only."""
    steps = workflow["jobs"]["full-pytest-sweep"]["steps"]
    for step in steps:
        if step.get("name") == "Run full pytest sweep":
            return step.get("run", "")
    raise AssertionError("Step 'Run full pytest sweep' not found in workflow")


def test_workflow_file_exists():
    assert WORKFLOW_PATH.exists(), f"Workflow not found: {WORKFLOW_PATH}"


def test_workflow_yaml_parses():
    data = _load_workflow()
    assert "jobs" in data
    assert "full-pytest-sweep" in data["jobs"]


def test_pytest_test_root_is_tests_dir():
    workflow = _load_workflow()
    cmd = _pytest_run_command(workflow)
    assert "tests/" in cmd, f"pytest command must use 'tests/' as root; got:\n{cmd}"


def test_no_forbidden_scope_reduction_flags():
    workflow = _load_workflow()
    cmd = _pytest_run_command(workflow)
    for flag in FORBIDDEN_PYTEST_FLAGS:
        assert flag not in cmd, (
            f"Forbidden scope-reduction flag '{flag}' found in pytest command.\n"
            f"Agents MAY NOT narrow sweep scope without explicit operator approval.\n"
            f"Command:\n{cmd}"
        )


def test_taxonomy_sentinel_comment_block_intact():
    raw = WORKFLOW_PATH.read_text()
    for snippet in REQUIRED_TAXONOMY_SNIPPETS:
        assert snippet in raw, (
            f"Taxonomy sentinel snippet missing from workflow file:\n  '{snippet}'\n"
            "Do not remove the 8-category failure taxonomy comment block."
        )


def test_collection_floor_regex_uses_slashed_format():
    """Assert collection-floor step uses the pytest 9.0.2 'X/Y tests collected' format.

    Pytest 9.0.2 changed summary output from '9390 tests collected' to
    '9390/9408 tests collected (18 deselected)'. The old '^[0-9]+ tests? collected'
    regex silently fails (grep exits non-zero, count stays empty, floor step crashes).
    This test prevents regression to the old format.
    """
    workflow = _load_workflow()
    steps = workflow["jobs"]["full-pytest-sweep"]["steps"]
    floor_step = None
    for step in steps:
        if step.get("name") == "Assert collection floor (>=400 tests)":
            floor_step = step
            break
    assert floor_step is not None, "Step 'Assert collection floor (>=400 tests)' not found"
    run_block = floor_step.get("run", "")
    # Must use slashed-format grep, not the old bare-number format
    assert "[0-9]+/[0-9]+ tests collected" in run_block, (
        "Collection-floor regex must match pytest 9.0.2's 'X/Y tests collected' format.\n"
        f"Got run block:\n{run_block}"
    )
    # Must NOT use the broken old pattern as the primary extraction
    assert "^[0-9]+ tests? collected" not in run_block, (
        "Old bare-count regex '^[0-9]+ tests? collected' still present — will fail on pytest 9.0.2."
    )
    # Must strip ANSI codes before grepping (pytest 9 emits colored output)
    assert r"\x1b" in run_block, (
        "Collection-floor command must strip ANSI escape codes via sed before grepping."
    )


def test_collection_floor_regex_matches_actual_pytest_output():
    """Live smoke: run the exact bash pipeline and assert count >= 400.

    This test reproduces the CI pipeline locally to catch future format changes
    before they reach CI. If pytest changes its summary format again, this test
    fails first (not the GitHub Actions job).
    """
    import subprocess
    import re

    # WORKFLOW_PATH is .github/workflows/full-pytest-sweep.yml; repo root is two levels up
    repo_root = WORKFLOW_PATH.parent.parent.parent
    raw = subprocess.run(
        ["python", "-m", "pytest", "--collect-only", "-q", "tests/"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    combined = raw.stdout + raw.stderr
    # Strip ANSI
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    clean = ansi_re.sub("", combined)
    # Match slashed format
    m = re.search(r"(\d+)/\d+ tests collected", clean)
    assert m is not None, (
        f"Could not find 'X/Y tests collected' in pytest --collect-only output.\n"
        f"Output tail:\n{clean[-500:]}"
    )
    count = int(m.group(1))
    assert count >= 400, (
        f"Collection floor breach: only {count} tests collected (floor: 400). "
        "Silent test drop or pytest format change detected."
    )
