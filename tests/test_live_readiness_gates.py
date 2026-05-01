# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/G1.yaml
# Purpose: R3 G1 live-readiness gate orchestrator regressions.
# Reuse: Run before any live-readiness, cutover, deployment, or operator-gate changes.
"""R3 G1 live-readiness gate tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts import live_readiness_check as lrc


def _passing_runner(_cmd):
    return subprocess.CompletedProcess(_cmd, 0, stdout="1 passed\n", stderr="")


def _signed_evidence(evidence_type: str, payload: dict, *, secret: str = "unit-secret") -> str:
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{evidence_type}\n{payload_hash}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return json.dumps(
        {
            "schema_version": 1,
            "evidence_type": evidence_type,
            "payload": payload,
            "payload_sha256": payload_hash,
            "hmac_sha256": signature,
        },
        sort_keys=True,
    )


def _evidence_root(tmp_path: Path) -> Path:
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "staged_live_smoke_2026-04-27.json").write_text(
        _signed_evidence(
            "staged_live_smoke",
            {
                "status": "PASS",
                "gates_passed": 16,
                "environment": "staged-live-smoke",
            },
        )
    )
    return root


def test_gate_registry_removes_g1_02_and_keeps_remaining_gate_ids_stable():
    assert len(lrc.GATES) == 16
    assert len({gate.gate_id for gate in lrc.GATES}) == 16
    assert {gate.gate_id for gate in lrc.GATES} == {f"G1-{idx:02d}" for idx in range(1, 18)} - {"G1-02"}
    assert all(gate.kind != "q1_evidence" for gate in lrc.GATES)
    assert any(gate.kind == "agent_docs_scan" for gate in lrc.GATES)


def test_missing_staged_smoke_evidence_fails_closed(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    report = lrc.run_readiness(evidence_roots=(empty,), runner=_passing_runner)

    assert report.status == lrc.FAIL
    assert report.live_deploy_authorized is False
    by_id = {gate.gate_id: gate for gate in report.gates}
    assert "G1-02" not in by_id
    assert report.staged_smoke_status == lrc.FAIL
    assert "missing staged-live-smoke" in report.staged_smoke_evidence


def test_legacy_sdk_gate_detects_nested_v1_imports_without_blocking_v2(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text("from py_clob_client.client import ClobClient\n")
    (src / "good.py").write_text("import py_clob_client_v2\n")

    gate = next(gate for gate in lrc.GATES if gate.kind == "legacy_sdk_scan")
    result = lrc._legacy_sdk_gate(gate, tmp_path)

    assert result.status == lrc.FAIL
    assert "bad.py" in result.evidence
    assert "good.py" not in result.evidence


def test_readiness_pass_requires_signed_active_gate_passes_and_staged_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv(lrc.EVIDENCE_HMAC_SECRET_ENV, "unit-secret")
    evidence = _evidence_root(tmp_path)

    report = lrc.run_readiness(evidence_roots=(evidence,), runner=_passing_runner)

    assert report.status == lrc.PASS
    assert report.gate_count == 16
    assert report.passed_gates == 16
    assert report.staged_smoke_status == lrc.PASS
    assert report.live_deploy_authorized is False


def test_unsigned_marker_files_do_not_satisfy_staged_smoke_evidence(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "staged_live_smoke_2026-04-27.json").write_text(
        '{"status":"PASS","gates_passed":17,"environment":"staged-live-smoke"}'
    )

    report = lrc.run_readiness(evidence_roots=(evidence,), runner=_passing_runner)

    by_id = {gate.gate_id: gate for gate in report.gates}
    assert "G1-02" not in by_id
    assert report.staged_smoke_status == lrc.FAIL


def test_command_gate_failure_makes_report_fail(tmp_path, monkeypatch):
    monkeypatch.setenv(lrc.EVIDENCE_HMAC_SECRET_ENV, "unit-secret")
    evidence = _evidence_root(tmp_path)

    def failing_runner(cmd):
        if "tests/test_unknown_side_effect.py::test_duplicate_retry_blocked_during_unknown" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="FAILED unknown gate\n", stderr="")
        return _passing_runner(cmd)

    report = lrc.run_readiness(evidence_roots=(evidence,), runner=failing_runner)

    assert report.status == lrc.FAIL
    by_id = {gate.gate_id: gate for gate in report.gates}
    assert by_id["G1-09"].status == lrc.FAIL
    assert "FAILED unknown gate" in by_id["G1-09"].evidence


def test_cli_help_is_safe_and_does_not_require_live_evidence():
    proc = subprocess.run(
        [sys.executable, "scripts/live_readiness_check.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "Run R3 G1 live-readiness gates" in proc.stdout
    assert "live_deploy_authorized" not in proc.stdout


def test_cli_evidence_root_override_is_test_only(tmp_path):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("ZEUS_TESTING", None)
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/live_readiness_check.py",
            "--evidence-root",
            str(tmp_path),
            "--no-run-commands",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert proc.returncode != 0
    assert "--evidence-root is test-only" in proc.stderr
