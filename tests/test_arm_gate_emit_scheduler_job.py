# Created: 2026-06-04
# Last reused or audited: 2026-06-04
# Authority basis: iron-rule-4 antibody (operator 2026-06-04) — the
#   settlement→ARM-evidence loop was WIRED (producer scripts/measure_arm_gate_settlement.py
#   --emit-artifact + consumer src/main.py::_assert_edli_arm_gate_artifact) but never
#   AUTOMATED: nothing RAN the producer, so state/edli_arm_gate_artifact.json could go
#   missing / stale (commit_sha != HEAD) → the boot gate fail-closes
#   ARM_GATE_ARTIFACT_MISSING/COMMIT_SHA_MISMATCH → the system is structurally un-armable
#   AND cannot even boot in canary/live. This wires a scheduler job (_arm_gate_emit_cycle,
#   @_scheduler_job("arm_gate_emit")) that re-emits the artifact on startup (re-stamping
#   commit_sha to the running HEAD) and every ~6h (refreshing as settlements accrue).
#
# Relationship under test (CROSSES the producer→consumer boundary, per Fitz methodology):
#   after the emit job runs, verify_edli_arm_gate_artifact (the boot-gate consumer)
#   reads the SAME artifact the job wrote and its presence/SHA checks PASS at the
#   running HEAD. The job NEVER manufactures ARM_ELIGIBLE on denied data, NEVER crashes
#   the daemon, and is a strict no-op when its flag is OFF (byte-identical rollback).
#
# Anti-fabrication / boot-guard regression: a prior scheduler-wiring change FATAL
# crash-looped the daemon because a boot guard (assert_writer_jobs_registered) AST-scans
# for writer jobs. test_boot_guard_still_passes_with_arm_gate_emit_job proves the real
# guard still passes with this job added (this job writes a FILE not a DB table, so it is
# OUT of that guard's scope and must NOT enter db_table_ownership.yaml).
"""Relationship tests: arm_gate_emit scheduler job ⟷ boot-gate consumer + boot guards."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import src.main as main
from src.events.live_profit_audit import verify_edli_arm_gate_artifact
from src.state.table_registry import assert_writer_jobs_registered


def _running_head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(Path(main.__file__).resolve().parent.parent),
        text=True,
    ).strip()


# ---------------------------------------------------------------------------
# (1) After the emit job runs, the artifact EXISTS and the boot-gate consumer
#     ACCEPTS it on presence/SHA at the running HEAD. The job re-stamps commit_sha
#     to HEAD — closing the "stale SHA → boot fail" break this antibody targets.
#     (The consumer still rejects on coverage_licensed:false — that is the HONEST
#     DENIED state asserted in test (2). Here we assert the SHA/presence layer the
#     job is responsible for.)
# ---------------------------------------------------------------------------
def test_emit_job_writes_artifact_consumer_accepts_sha_at_head(tmp_path, monkeypatch):
    artifact_path = tmp_path / "edli_arm_gate_artifact.json"
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda name, default=None: {
            "enabled": True,
            "edli_arm_gate_emit_enabled": True,
            "edli_arm_gate_artifact_path": str(artifact_path),
        }
        if name == "edli_v1"
        else (default if default is not None else {}),
    )

    main._arm_gate_emit_cycle()

    assert artifact_path.exists(), "emit job did not write the artifact"
    artifact = json.loads(artifact_path.read_text())

    head = _running_head_sha()
    assert artifact["production_n"] == artifact["gate_pass_n"]
    # The job's job: re-stamp commit_sha to the running HEAD so the SHA check passes.
    assert str(artifact.get("commit_sha")).strip() == head, (
        "emit job did not stamp commit_sha to the running HEAD — the stale-SHA "
        "break is not closed"
    )
    # Verify the SHA/presence layer of the boot-gate consumer is satisfied: it must
    # NOT reject for MISSING / MALFORMED / SHA_MISMATCH / SCHEMA / FIELD_MISSING.
    verified = verify_edli_arm_gate_artifact(artifact, head_sha=head)
    assert "ARTIFACT_MISSING" not in (verified.reason or "")
    assert "COMMIT_SHA_MISMATCH" not in (verified.reason or "")
    assert "SCHEMA_INVALID" not in (verified.reason or "")
    assert "FIELD_MISSING" not in (verified.reason or "")
    assert "MALFORMED" not in (verified.reason or "")


# ---------------------------------------------------------------------------
# (2) On the current (insufficient/denied) cohort, the emitted artifact carries
#     the BLOCKING verdict and the consumer REJECTS it — never ELIGIBLE. The
#     antibody automates re-emission WITHOUT ever weakening the arm verdict.
# ---------------------------------------------------------------------------
def test_emit_job_on_denied_data_stays_blocking_consumer_rejects(tmp_path, monkeypatch):
    artifact_path = tmp_path / "edli_arm_gate_artifact.json"
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda name, default=None: {
            "enabled": True,
            "edli_arm_gate_emit_enabled": True,
            "edli_arm_gate_artifact_path": str(artifact_path),
        }
        if name == "edli_v1"
        else (default if default is not None else {}),
    )

    main._arm_gate_emit_cycle()
    artifact = json.loads(artifact_path.read_text())
    assert artifact["production_n"] == artifact["gate_pass_n"]

    # Honest DENIED state: ev<=0 OR coverage not licensed → consumer rejects.
    verified = verify_edli_arm_gate_artifact(artifact, head_sha=_running_head_sha())
    assert not verified.ok, (
        "emit job produced a NON-BLOCKING artifact on denied data — arm verdict "
        "was weakened (forbidden)"
    )
    blocks = (float(artifact["capital_weighted_ev"]) <= 0.0) or (
        artifact["coverage_licensed"] is not True
    )
    assert blocks, "artifact is not blocking on denied cohort"


# ---------------------------------------------------------------------------
# (3) BOOT-GUARD REGRESSION: the real writer-jobs boot guard still PASSES with the
#     arm_gate_emit job added. This is the regression that crash-looped the daemon
#     before. The job writes a FILE (no DB table) so it is OUT of this guard's scope
#     — it must NOT be added to db_table_ownership.yaml — and the guard must remain
#     green against the real ingest_main.py source.
# ---------------------------------------------------------------------------
def test_boot_guard_still_passes_with_arm_gate_emit_job():
    # Must not raise. (Guard scans ingest_main.py + db_table_ownership.yaml; the
    # arm_gate_emit job lives in src/main.py and owns no table, so it is invisible
    # to this guard — exactly why it cannot trip the FATAL crash loop.)
    assert_writer_jobs_registered()

    # Antibody: the arm_gate_emit job must NOT have been registered as a daemon_writer
    # of any DB table (that would wrongly pull it into the ingest_main guard's scope).
    import yaml

    repo_root = Path(main.__file__).resolve().parent.parent
    ownership = yaml.safe_load(
        (repo_root / "architecture" / "db_table_ownership.yaml").read_text()
    )
    writers = {
        str(t.get("daemon_writer", "")).strip()
        for t in ownership.get("tables", [])
        if isinstance(t, dict)
    }
    assert "arm_gate_emit" not in writers, (
        "arm_gate_emit must not be a db_table_ownership daemon_writer — it writes a "
        "FILE, not a table; registering it there pulls it into assert_writer_jobs_"
        "registered's scope and risks the FATAL boot crash loop"
    )


# ---------------------------------------------------------------------------
# (4) FLAG-OFF ROLLBACK: with edli_arm_gate_emit_enabled=False the job is a strict
#     no-op — no artifact write — byte-identical to today's behavior.
# ---------------------------------------------------------------------------
def test_emit_job_flag_off_is_noop(tmp_path, monkeypatch):
    artifact_path = tmp_path / "edli_arm_gate_artifact.json"
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda name, default=None: {
            "enabled": True,
            "edli_arm_gate_emit_enabled": False,
            "edli_arm_gate_artifact_path": str(artifact_path),
        }
        if name == "edli_v1"
        else (default if default is not None else {}),
    )

    main._arm_gate_emit_cycle()

    assert not artifact_path.exists(), (
        "flag-OFF must be a no-op — no artifact written (safe rollback broken)"
    )


# ---------------------------------------------------------------------------
# (5) FAILURE ISOLATION: a producer failure (subprocess non-zero / timeout / any
#     exception) must NEVER propagate out of the job — the daemon keeps running.
# ---------------------------------------------------------------------------
def test_emit_job_never_propagates_on_failure(tmp_path, monkeypatch):
    artifact_path = tmp_path / "edli_arm_gate_artifact.json"
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda name, default=None: {
            "enabled": True,
            "edli_arm_gate_emit_enabled": True,
            "edli_arm_gate_artifact_path": str(artifact_path),
        }
        if name == "edli_v1"
        else (default if default is not None else {}),
    )

    def _boom(*a, **k):
        raise subprocess.CalledProcessError(1, ["python", "boom"])

    monkeypatch.setattr(main.subprocess, "run", _boom)

    # Must NOT raise — failure isolation. (No artifact written on failure.)
    main._arm_gate_emit_cycle()
    assert not artifact_path.exists()
