# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Proving tests for the Phase D structural-blocker enforcers.

Referenced by architecture/topology_enforcement.yaml as `proving_test` for:
  - stdlib_shadowing_gate
  - source_rationale_delta_gate
  - db_table_delta_gate
  - high_risk_surface_no_context_pack
  - new_test_unregistered (via integrity check)
  - duplicate_active_authority
  - tier0_paired_relationship_test_gate

Each enforcer has at least one positive test (rule fires on synthetic
violation) and one negative test (rule silent on clean input).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_CI = REPO_ROOT / "scripts" / "ci"


def _run(script: str, *args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_CI / script), *args],
        capture_output=True, text=True, timeout=60, cwd=cwd, check=False,
    )


# ---------------------------------------------------------------------------
# stdlib_shadowing_gate
# ---------------------------------------------------------------------------


def test_stdlib_shadowing_passes_on_clean_repo():
    r = _run("check_stdlib_shadowing.py")
    assert r.returncode == 0
    assert "OK" in r.stdout


def test_stdlib_shadowing_detects_synthetic_violation(tmp_path: Path):
    pkg = tmp_path / "scripts" / "fake_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "dataclasses.py").write_text("# shadows stdlib\n")
    r = _run("check_stdlib_shadowing.py", "--repo-root", str(tmp_path))
    assert r.returncode == 1
    assert "dataclasses" in r.stdout
    assert "shadows stdlib" in r.stdout


def test_stdlib_shadowing_silent_on_non_shadowing_name(tmp_path: Path):
    pkg = tmp_path / "scripts" / "fake_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "topology_models.py").write_text("# safe\n")
    r = _run("check_stdlib_shadowing.py", "--repo-root", str(tmp_path))
    assert r.returncode == 0


# ---------------------------------------------------------------------------
# source_rationale_delta_gate
# ---------------------------------------------------------------------------


def test_source_rationale_delta_passes_on_no_new_sources():
    r = _run(
        "check_source_rationale_delta.py",
        "--changed-files", "tests/topology/test_context_pack_schema.py",
    )
    assert r.returncode == 0


def test_source_rationale_delta_detects_new_provider_file(tmp_path: Path):
    # Set up minimal repo skeleton
    (tmp_path / "architecture").mkdir()
    (tmp_path / "architecture" / "source_rationale.yaml").write_text(
        "schema_version: 1\nsources: {existing_known: {}}\n"
    )
    (tmp_path / "src" / "data").mkdir(parents=True)
    new_provider = tmp_path / "src" / "data" / "newprov_client.py"
    new_provider.write_text("# fake new provider\n")
    r = _run(
        "check_source_rationale_delta.py",
        "--repo-root", str(tmp_path),
        "--changed-files", "src/data/newprov_client.py",
    )
    assert r.returncode == 1
    assert "newprov" in r.stdout


# ---------------------------------------------------------------------------
# db_table_delta_gate
# ---------------------------------------------------------------------------


def test_db_table_delta_passes_on_no_new_tables():
    r = _run(
        "check_db_table_delta.py",
        "--changed-files", "README.md",
    )
    assert r.returncode == 0


def test_db_table_delta_detects_new_create_table(tmp_path: Path):
    (tmp_path / "architecture").mkdir()
    (tmp_path / "architecture" / "db_table_ownership.yaml").write_text(
        "schema_version: 1\nzeus_world:\n  known_table_a: {}\n"
    )
    src = tmp_path / "src" / "state" / "schema"
    src.mkdir(parents=True)
    (src / "new_migration.py").write_text(
        'sql = "CREATE TABLE IF NOT EXISTS brand_new_table (id INTEGER PRIMARY KEY)"\n'
    )
    r = _run(
        "check_db_table_delta.py",
        "--repo-root", str(tmp_path),
        "--changed-files", "src/state/schema/new_migration.py",
    )
    assert r.returncode == 1
    assert "brand_new_table" in r.stdout


def test_db_table_delta_silent_on_known_table(tmp_path: Path):
    (tmp_path / "architecture").mkdir()
    (tmp_path / "architecture" / "db_table_ownership.yaml").write_text(
        "schema_version: 1\nzeus_world:\n  known_table_a: {}\n"
    )
    src = tmp_path / "src" / "state" / "schema"
    src.mkdir(parents=True)
    (src / "table_known.py").write_text(
        'sql = "CREATE TABLE known_table_a (id INTEGER)"\n'
    )
    r = _run(
        "check_db_table_delta.py",
        "--repo-root", str(tmp_path),
        "--changed-files", "src/state/schema/table_known.py",
    )
    assert r.returncode == 0


def test_db_table_delta_ignores_test_fixture_strings(tmp_path: Path):
    """Phase D.1 antibody: PR #345 false positive — regex matched CREATE TABLE
    literal strings inside test fixtures and this script's own docstring.
    Restrict scanner to schema-defining paths so non-schema files with literal
    CREATE TABLE tokens (test fixtures, error messages) are not flagged."""
    (tmp_path / "architecture").mkdir()
    (tmp_path / "architecture" / "db_table_ownership.yaml").write_text(
        "schema_version: 1\nzeus_world: {}\n"
    )
    # Test file outside src/state/schema/ — should be ignored even with CREATE TABLE
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fixture.py").write_text(
        'def test_x(tmp_path):\n'
        '    sql = "CREATE TABLE fixture_table_a (id INTEGER)"\n'
        '    # And a docstring example: CREATE TABLE for unregistered table\n'
    )
    # Script file outside schema dirs — also ignored
    ci = tmp_path / "scripts" / "ci"
    ci.mkdir(parents=True)
    (ci / "fake_check.py").write_text(
        '"""Comment about CREATE TABLE handling."""\n'
    )
    r = _run(
        "check_db_table_delta.py",
        "--repo-root", str(tmp_path),
        "--changed-files",
        "tests/test_fixture.py", "scripts/ci/fake_check.py",
    )
    assert r.returncode == 0, f"false positive regression: {r.stdout}"


def test_db_table_delta_sql_reserved_words_not_treated_as_tables(tmp_path: Path):
    """`CREATE TABLE for ...` in docstring should never produce a finding
    named `for`."""
    (tmp_path / "architecture").mkdir()
    (tmp_path / "architecture" / "db_table_ownership.yaml").write_text(
        "schema_version: 1\nzeus_world: {}\n"
    )
    schema = tmp_path / "src" / "state" / "schema"
    schema.mkdir(parents=True)
    # Schema file with malformed/docstring SQL — reserved keyword should be skipped
    (schema / "weird.py").write_text(
        '"""Note: CREATE TABLE for unregistered would be bad."""\n'
        '# CREATE TABLE as if we forgot\n'
    )
    r = _run(
        "check_db_table_delta.py",
        "--repo-root", str(tmp_path),
        "--changed-files", "src/state/schema/weird.py",
    )
    # Reserved words ("for", "as") must not produce findings
    assert "table 'for'" not in r.stdout.lower()
    assert "table 'as'" not in r.stdout.lower()
    assert r.returncode == 0


# ---------------------------------------------------------------------------
# Orchestrator — bounded by --include to avoid running every rule
# (a full-suite invocation must remain safe; see fork-bomb antibody below)
# ---------------------------------------------------------------------------


def test_orchestrator_include_single_rule_runs_clean():
    """Run a single non-recursive rule via --include."""
    r = _run("check_topology_structural_blockers.py",
             "--include", "workflow_refs_exist",
             "--json")
    import json
    data = json.loads(r.stdout)
    assert data["failure_count"] == 0
    assert data["no_override_failure_count"] == 0


def test_orchestrator_unknown_include_exits_2():
    r = _run("check_topology_structural_blockers.py",
             "--include", "nonexistent_rule")
    assert r.returncode == 2


def test_orchestrator_emits_json():
    r = _run("check_topology_structural_blockers.py",
             "--include", "workflow_refs_exist", "--json")
    import json
    data = json.loads(r.stdout)
    assert "results" in data
    assert "failure_count" in data
    assert "no_override_failure_count" in data


# ---------------------------------------------------------------------------
# FORK-BOMB ANTIBODY (2026-05-26): operator crash report
# Earlier topology_enforcement.yaml listed the orchestrator as the enforcer
# for 9 rules. The orchestrator subprocess-invokes each enforcer; when the
# enforcer was itself, each invocation spawned 9 more, exponentially.
# These tests prove the bug stays dead.
# ---------------------------------------------------------------------------


def test_yaml_does_not_self_reference_orchestrator():
    """No blocking_structural or advisory rule may declare the orchestrator
    as its own enforcer. Self-referencing rules MUST be REVIEW_REQUIRED
    until a dedicated enforcer ships."""
    import yaml
    enforce_path = REPO_ROOT / "architecture" / "topology_enforcement.yaml"
    with enforce_path.open() as f:
        doc = yaml.safe_load(f)
    for category in ("blocking_structural", "advisory"):
        for rule in (doc.get(category) or []):
            enforcer = rule.get("enforcer", "")
            assert enforcer != "scripts/ci/check_topology_structural_blockers.py", (
                f"rule {rule.get('id')} in {category} self-references the orchestrator "
                f"(fork-bomb hazard). Use a dedicated enforcer or REVIEW_REQUIRED."
            )


def test_orchestrator_self_guard_when_invoked_via_yaml(tmp_path: Path):
    """If a synthetic yaml DOES list the orchestrator as an enforcer, the
    self-guard must catch it and NOT subprocess-invoke itself."""
    # Build a tiny repo with a self-referencing yaml
    arch = tmp_path / "architecture"
    arch.mkdir()
    (arch / "topology_enforcement.yaml").write_text(
        "schema_version: 1\n"
        "blocking_structural:\n"
        "  - id: self_recursive\n"
        "    description: this would fork-bomb\n"
        "    enforcer: scripts/ci/check_topology_structural_blockers.py\n"
        "    proving_test: tests/topology/test_structural_blockers.py\n"
        "    override_allowed: true\n"
        "    severity: blocking\n"
        "    owner: test\n"
        "no_override_rules: []\n"
    )
    # Symlink the orchestrator script into the synthetic repo so the path
    # resolves; the guard must still refuse.
    scripts = tmp_path / "scripts" / "ci"
    scripts.mkdir(parents=True)
    orchestrator = REPO_ROOT / "scripts" / "ci" / "check_topology_structural_blockers.py"
    (scripts / "check_topology_structural_blockers.py").symlink_to(orchestrator)
    # Run; must terminate fast (<10s) without exploding
    import time
    t0 = time.time()
    r = _run("check_topology_structural_blockers.py",
             "--repo-root", str(tmp_path),
             "--json")
    elapsed = time.time() - t0
    assert elapsed < 10, f"orchestrator took {elapsed:.1f}s — possible fork-bomb regression"
    import json
    data = json.loads(r.stdout)
    # The rule must be marked skipped, not invoked
    assert len(data["results"]) == 1
    assert data["results"][0].get("skipped") is True
    assert "SELF-RECURSION GUARD" in data["results"][0]["stderr"]
    assert data["failure_count"] == 0
    assert data["no_override_failure_count"] == 0


def test_orchestrator_handles_review_required_enforcer(tmp_path: Path):
    """enforcer: REVIEW_REQUIRED placeholder → skipped, exit 0, no error."""
    arch = tmp_path / "architecture"
    arch.mkdir()
    (arch / "topology_enforcement.yaml").write_text(
        "schema_version: 1\n"
        "blocking_structural:\n"
        "  - id: placeholder\n"
        "    description: not yet implemented\n"
        "    enforcer: REVIEW_REQUIRED\n"
        "    proving_test: REVIEW_REQUIRED\n"
        "    override_allowed: false\n"
        "    severity: blocking\n"
        "    owner: test\n"
        "no_override_rules: []\n"
    )
    r = _run("check_topology_structural_blockers.py",
             "--repo-root", str(tmp_path),
             "--json")
    assert r.returncode == 0
    import json
    data = json.loads(r.stdout)
    assert data["results"][0].get("skipped") is True


# ---------------------------------------------------------------------------
# new_test_unregistered (validated via integrity check + test_topology.yaml)
# ---------------------------------------------------------------------------


def test_all_new_phase_d_tests_registered_in_test_topology():
    """Per new_test_unregistered no_override rule: every tests/ file
    Phase D ships must appear in architecture/test_topology.yaml#trusted_tests."""
    import yaml
    with (REPO_ROOT / "architecture" / "test_topology.yaml").open() as f:
        tt = yaml.safe_load(f)
    trusted = tt["test_trust_policy"]["trusted_tests"]
    required = [
        "tests/topology/test_structural_blockers.py",
        "tests/ci/test_structural_blockers.py",
        "tests/ci/test_context_pack_overrides.py",
    ]
    for path in required:
        assert path in trusted, (
            f"{path} not registered in architecture/test_topology.yaml"
        )


# ---------------------------------------------------------------------------
# duplicate_active_authority — PARTIAL coverage; full enforcer is REVIEW_REQUIRED
# ---------------------------------------------------------------------------
#
# The duplicate_active_authority rule says "two files cannot both claim active
# authority for the same fact class". A real implementation would parse the
# topology authority graph (architecture/topology.yaml, architecture/docs_registry.yaml,
# scoped AGENTS files, etc.) and detect conflicting active_authority claims.
# That's deferred to a Phase E+ dedicated enforcer (currently REVIEW_REQUIRED
# in architecture/topology_enforcement.yaml).
#
# Below is a SMOKE check on one easy dimension: workflow names must be unique.
# It's labeled accordingly and does NOT claim to cover the full rule.


def test_workflow_names_are_unique_smoke():
    """Smoke check related to duplicate_active_authority: each workflow yaml
    has a unique top-level `name:` field. Full rule coverage (cross-file
    authority graph) requires a dedicated enforcer (REVIEW_REQUIRED in
    architecture/topology_enforcement.yaml#duplicate_active_authority)."""
    wf_dir = REPO_ROOT / ".github" / "workflows"
    names: dict[str, str] = {}
    import yaml
    for path in wf_dir.glob("*.yml"):
        with path.open() as f:
            doc = yaml.safe_load(f) or {}
        name = doc.get("name")
        if name:
            assert name not in names, (
                f"workflow name {name!r} duplicated: {names[name]} and {path.name}"
            )
            names[name] = path.name


def test_duplicate_active_authority_rule_acknowledges_review_required():
    """The duplicate_active_authority rule in topology_enforcement.yaml must
    currently be REVIEW_REQUIRED until a dedicated enforcer ships. This
    assertion prevents accidentally pointing it at the orchestrator (fork
    bomb hazard) or at a script that doesn't actually implement it.

    2026-06-13: rule moved from blocking_structural → advisory because a
    blocking rule with a REVIEW_REQUIRED enforcer enforces nothing but bricks
    the required CI gate (meta-integrity check). Search both sections."""
    import yaml
    enforce_path = REPO_ROOT / "architecture" / "topology_enforcement.yaml"
    with enforce_path.open() as f:
        enforce = yaml.safe_load(f)
    all_rules = list(enforce.get("blocking_structural") or []) + list(enforce.get("advisory") or [])
    rule = next(
        (r for r in all_rules if r.get("id") == "duplicate_active_authority"),
        None,
    )
    assert rule is not None, "duplicate_active_authority rule must exist in yaml"
    assert rule["enforcer"] == "REVIEW_REQUIRED", (
        f"duplicate_active_authority enforcer must be REVIEW_REQUIRED "
        f"until dedicated enforcer ships; got {rule['enforcer']!r}"
    )
