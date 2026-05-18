# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: CI_PR_159_CRITIC.md P2/M4; CI_IMPROVEMENT_DESIGN.md

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
