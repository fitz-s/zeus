# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml#blocking_structural:expired_override
#                  architecture/ci_overrides.yaml
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Proving tests for the expired_override no_override rule.

Referenced by architecture/topology_enforcement.yaml as
`proving_test: tests/ci/test_context_pack_overrides.py` for
expired_override.

Covers every failure_rule in architecture/ci_overrides.yaml:
  - override_expired, override_owner_missing, override_reason_empty,
    override_risk_accepted_empty, override_path_scope_wider_than_changed_files,
    override_followup_missing_for_p0_p1_surface, override_reviewer_approval_missing,
    override_attempts_no_override_rule, override_expiry_too_distant,
    override_id_collision
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "check_context_pack_overrides.py"


def _build_repo(tmp_path: Path, overrides: list[dict], enforcement_no_override: list[str] | None = None) -> Path:
    """Stand up a minimal repo with architecture/ci_overrides.yaml + topology_enforcement.yaml."""
    arch = tmp_path / "architecture"
    arch.mkdir()
    (arch / "ci_overrides.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "overrides": overrides,
    }))
    enforce = {
        "schema_version": 1,
        "blocking_structural": [],
        "no_override_rules": enforcement_no_override or [],
    }
    (arch / "topology_enforcement.yaml").write_text(yaml.safe_dump(enforce))
    return tmp_path


def _run(repo: Path, *args: str, today: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), "--repo-root", str(repo)]
    if today:
        cmd.extend(["--today", today])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)


def _valid_override(**overrides) -> dict:
    """A baseline well-formed override; tests override individual fields."""
    base = {
        "id": "OVR-2026-05-26-test",
        "rule_id": "context_pack_references_missing_file",
        "applies_to": {"changed_files": ["src/foo.py"], "pr_number": 1, "branch_pattern": None},
        "owner": "tester",
        "reviewer_approval_required": False,
        "approved_by": [],
        "reason": "test override",
        "risk_accepted": "limited test scope",
        "expiry_date": "2026-06-01",
        "follow_up": {"required": True, "issue_or_pr": "https://github.com/x/y/issues/1", "due_date": "2026-06-01"},
        "allowed_changed_files": ["src/foo.py"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Clean cases
# ---------------------------------------------------------------------------


def test_empty_overrides_passes(tmp_path: Path):
    repo = _build_repo(tmp_path, [])
    r = _run(repo)
    assert r.returncode == 0
    assert "no violations" in r.stdout


def test_valid_override_passes(tmp_path: Path):
    repo = _build_repo(tmp_path, [_valid_override()])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 0, f"valid override failed: {r.stdout}"


# ---------------------------------------------------------------------------
# Each failure rule
# ---------------------------------------------------------------------------


def test_override_expired(tmp_path: Path):
    repo = _build_repo(tmp_path, [_valid_override(expiry_date="2026-05-01")])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_expired" in r.stdout


def test_override_owner_missing(tmp_path: Path):
    repo = _build_repo(tmp_path, [_valid_override(owner="")])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_owner_missing" in r.stdout


def test_override_reason_empty(tmp_path: Path):
    repo = _build_repo(tmp_path, [_valid_override(reason="")])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_reason_empty" in r.stdout


def test_override_risk_accepted_empty(tmp_path: Path):
    repo = _build_repo(tmp_path, [_valid_override(risk_accepted="")])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_risk_accepted_empty" in r.stdout


def test_override_path_scope_wider_than_changed_files(tmp_path: Path):
    ov = _valid_override(
        applies_to={"changed_files": ["src/foo.py"]},
        allowed_changed_files=["src/foo.py", "src/bar.py"],   # extra entry
    )
    repo = _build_repo(tmp_path, [ov])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_path_scope_wider_than_changed_files" in r.stdout


def test_override_followup_missing_for_p0_p1_surface(tmp_path: Path):
    ov = _valid_override(
        risk_tier="T0",
        follow_up={"required": False, "issue_or_pr": "", "due_date": ""},
    )
    repo = _build_repo(tmp_path, [ov])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_followup_missing_for_p0_p1_surface" in r.stdout


def test_override_reviewer_approval_missing(tmp_path: Path):
    ov = _valid_override(reviewer_approval_required=True, approved_by=[])
    repo = _build_repo(tmp_path, [ov])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_reviewer_approval_missing" in r.stdout


def test_override_attempts_no_override_rule(tmp_path: Path):
    ov = _valid_override(rule_id="stdlib_shadowing_gate")
    repo = _build_repo(
        tmp_path, [ov], enforcement_no_override=["stdlib_shadowing_gate"]
    )
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_attempts_no_override_rule" in r.stdout


def test_override_expiry_too_distant(tmp_path: Path):
    """expiry > 60 days from anchor (created_at OR today) fails."""
    ov = _valid_override(
        expiry_date="2026-09-01",                  # ~98 days from 2026-05-26
        created_at="2026-05-26",
    )
    repo = _build_repo(tmp_path, [ov])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_expiry_too_distant" in r.stdout


def test_override_id_collision(tmp_path: Path):
    o1 = _valid_override(id="OVR-DUP")
    o2 = _valid_override(id="OVR-DUP")
    repo = _build_repo(tmp_path, [o1, o2])
    r = _run(repo, today="2026-05-26")
    assert r.returncode == 1
    assert "override_id_collision" in r.stdout


# ---------------------------------------------------------------------------
# JSON mode
# ---------------------------------------------------------------------------


def test_emits_json(tmp_path: Path):
    repo = _build_repo(tmp_path, [_valid_override(owner="")])
    r = _run(repo, "--json", today="2026-05-26")
    import json
    payload = json.loads(r.stdout)
    assert "violations" in payload
    assert payload["count"] >= 1
