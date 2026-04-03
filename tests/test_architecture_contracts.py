from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]

def load_yaml(path: str) -> dict:
    with open(ROOT / path) as f:
        return yaml.safe_load(f)

def test_principal_authority_files_exist():
    required = [
        "docs/architecture/zeus_durable_architecture_spec.md",
        "docs/governance/zeus_change_control_constitution.md",
        "architecture/kernel_manifest.yaml",
        "architecture/invariants.yaml",
        "architecture/zones.yaml",
        "architecture/negative_constraints.yaml",
    ]
    for rel in required:
        assert (ROOT / rel).exists(), rel

def test_strategy_key_manifest_is_frozen():
    kernel = load_yaml("architecture/kernel_manifest.yaml")
    atom = kernel["semantic_atoms"]["strategy_key"]
    assert atom["frozen"] is True
    assert atom["allowed"] == [
        "settlement_capture",
        "shoulder_sell",
        "center_buy",
        "opening_inertia",
    ]

def test_negative_constraints_include_no_local_close():
    negative = load_yaml("architecture/negative_constraints.yaml")
    ids = {item["id"] for item in negative["constraints"]}
    assert "NC-04" in ids

def test_negative_constraints_cover_strategy_fallback():
    negative = load_yaml("architecture/negative_constraints.yaml")
    ids = {item["id"] for item in negative["constraints"]}
    assert "NC-03" in ids

def test_risk_actions_exist_in_schema():
    sql = (ROOT / "migrations/2026_04_02_architecture_kernel.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS risk_actions" in sql
    assert "threshold_multiplier" in sql
    assert "allocation_multiplier" in sql

def test_schema_has_append_only_triggers():
    sql = (ROOT / "migrations/2026_04_02_architecture_kernel.sql").read_text()
    assert "position_events is append-only" in sql

def test_zone_model_declares_k0_and_k3():
    zones = load_yaml("architecture/zones.yaml")
    assert "K0_frozen_kernel" in zones["zones"]
    assert "K3_extension" in zones["zones"]

def test_semgrep_rules_cover_core_forbidden_moves():
    text = (ROOT / "architecture/ast_rules/semgrep_zeus.yml").read_text()
    for rule_id in (
        "zeus-no-direct-close-from-engine",
        "zeus-no-memory-only-control-state",
        "zeus-no-strategy-default-fallback",
    ):
        assert rule_id in text

def test_advisory_gate_workflow_freezes_verdict():
    workflow = load_yaml(".github/workflows/architecture_advisory_gates.yml")
    jobs = workflow["jobs"]
    triggers = workflow.get("on") or workflow.get(True) or {}

    advisory = {"semgrep-zeus", "replay-parity"}

    for job_name in advisory:
        assert jobs[job_name].get("continue-on-error") is True
        env = jobs[job_name]["env"]
        assert env["GATE_OWNER"]
        assert env["GATE_RATIONALE"]
        assert "Promote only after" in env["GATE_REVIEW_CONDITION"]

    policy_env = jobs["advisory-gate-policy"]["env"]
    assert policy_env["GATE_OWNER"] == "P-GATE-01 gate owner"
    assert policy_env["GATE_RATIONALE"]
    assert "validates verdict drift" in policy_env["GATE_REVIEW_CONDITION"]

    trigger_paths = set(triggers["pull_request"]["paths"])
    assert "work_packets/**" in trigger_paths
    assert "scripts/check_*" in trigger_paths
    assert "tests/test_architecture_contracts.py" in trigger_paths

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_advisory_gates.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
