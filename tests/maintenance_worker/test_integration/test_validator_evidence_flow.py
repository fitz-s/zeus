# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §7 P5.4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Evidence trail"
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Pre-Action Validator"
"""
Integration tests — ActionValidator + EvidenceWriter flow.

Exercises the two sub-systems that produce the evidence trail:
  (a) EvidenceWriter writes all expected artifacts for a complete tick
  (b) refuse_fatal writes errors.tsv to evidence_dir (Path A)
  (c) write_guards_tsv records guard events in TrailContext
  (d) write_proposal emits proposals/<task_id>.md and updates task_events
  (e) write_applied_row emits applied/<task_id>.tsv
  (f) write_summary produces SUMMARY.md with guard + task summaries
  (g) write_exit_code writes exit_code file
  (h) write_proposal_diff emits proposals/<task_id>.diff (SEV-3b)
  (i) write_rollback_recipe emits applied/<task_id>.rollback.json
  (j) Validator FORBIDDEN_PATH → refuse_fatal → errors.tsv written at evidence_dir root

All tests are unit/integration level — no live engine tick required.
EvidenceWriter is exercised directly; refuse_fatal is tested with mocked sys.exit.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.core.evidence_writer import EvidenceWriter, TrailContext
from maintenance_worker.core.refusal import refuse_fatal
from maintenance_worker.core.validator import ActionValidator
from maintenance_worker.types.modes import RefusalReason
from maintenance_worker.types.operations import Operation
from maintenance_worker.types.results import ValidatorResult
from maintenance_worker.types.specs import EngineConfig, TickContext


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_engine_config(tmp_path: Path) -> EngineConfig:
    repo_root = tmp_path / "repo"
    state_dir = tmp_path / "state"
    evidence_dir = tmp_path / "evidence"
    repo_root.mkdir(); state_dir.mkdir(); evidence_dir.mkdir()
    return EngineConfig(
        repo_root=repo_root,
        state_dir=state_dir,
        evidence_dir=evidence_dir,
        task_catalog_path=tmp_path / "TASK_CATALOG.yaml",
        safety_contract_path=tmp_path / "SAFETY_CONTRACT.md",
        live_default=False,
        scheduler="cron",
        notification_channel="none",
    )


def _make_tick_ctx(config: EngineConfig) -> TickContext:
    return TickContext(
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="CRON",
    )


def _open_trail(writer: EvidenceWriter, evidence_dir: Path) -> TrailContext:
    return writer.open_trail(date.today(), evidence_dir)


# ---------------------------------------------------------------------------
# (a) EvidenceWriter writes config_snapshot.json
# ---------------------------------------------------------------------------


def test_evidence_writer_config_snapshot(tmp_path: Path) -> None:
    """write_config_snapshot produces a valid JSON file in the trail dir."""
    config = _make_engine_config(tmp_path)
    writer = EvidenceWriter()
    ctx = _open_trail(writer, config.evidence_dir)

    writer.write_config_snapshot(ctx, config)

    snapshot_path = ctx.trail_dir / "config_snapshot.json"
    assert snapshot_path.exists(), "config_snapshot.json must exist after write"
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "repo_root" in data, "config_snapshot.json must contain repo_root"
    assert "live_default" in data, "config_snapshot.json must contain live_default"


# ---------------------------------------------------------------------------
# (b) refuse_fatal writes errors.tsv to evidence_dir (Path A)
# ---------------------------------------------------------------------------


def test_refuse_fatal_writes_errors_tsv(tmp_path: Path) -> None:
    """
    refuse_fatal() writes a TSV row to evidence_dir/errors.tsv before exiting.

    Path A invariant: only errors.tsv is written; no quarantine file is touched.
    """
    config = _make_engine_config(tmp_path)
    ctx = _make_tick_ctx(config)

    with patch.object(sys, "exit") as mock_exit:
        refuse_fatal(RefusalReason.KILL_SWITCH, ctx, "test kill switch")

    errors_tsv = config.evidence_dir / "errors.tsv"
    assert errors_tsv.exists(), "errors.tsv must exist after refuse_fatal"

    content = errors_tsv.read_text(encoding="utf-8")
    assert ctx.run_id in content, "errors.tsv must contain the run_id"
    assert "KILL_SWITCH" in content, "errors.tsv must contain the reason"
    assert "test kill switch" in content, "errors.tsv must contain the message"

    # Verify sys.exit was called with a non-zero code
    mock_exit.assert_called_once()
    exit_code = mock_exit.call_args[0][0]
    assert exit_code > 0, f"refuse_fatal exit code must be >0; got {exit_code}"


# ---------------------------------------------------------------------------
# (c) write_guards_tsv records guard events in TrailContext
# ---------------------------------------------------------------------------


def test_evidence_writer_guards_tsv(tmp_path: Path) -> None:
    """write_guards_tsv writes guards.tsv and populates ctx.guard_events."""
    config = _make_engine_config(tmp_path)
    writer = EvidenceWriter()
    ctx = _open_trail(writer, config.evidence_dir)

    report = [
        {"guard_name": "disk_space", "ok": True, "reason": "enough space", "details": {}},
        {"guard_name": "dirty_repo", "ok": False, "reason": "uncommitted changes", "details": {}},
    ]
    writer.write_guards_tsv(ctx, report)

    guards_path = ctx.trail_dir / "guards.tsv"
    assert guards_path.exists(), "guards.tsv must exist after write_guards_tsv"

    content = guards_path.read_text(encoding="utf-8")
    assert "disk_space" in content
    assert "dirty_repo" in content
    assert "TRUE" in content    # ok=True row
    assert "FALSE" in content   # ok=False row

    # guard_events must be populated
    assert len(ctx.guard_events) == 2
    names = [g[0] for g in ctx.guard_events]
    assert "disk_space" in names
    assert "dirty_repo" in names


# ---------------------------------------------------------------------------
# (d) write_proposal emits proposals/<task_id>.md and updates task_events
# ---------------------------------------------------------------------------


def test_evidence_writer_proposal_md(tmp_path: Path) -> None:
    """write_proposal creates proposals/<task_id>.md and appends to task_events."""
    config = _make_engine_config(tmp_path)
    writer = EvidenceWriter()
    ctx = _open_trail(writer, config.evidence_dir)

    from maintenance_worker.types.specs import ProposalManifest
    manifest = ProposalManifest(
        task_id="cleanup-scratch",
        proposed_moves=(),
        proposed_deletes=(Path("/tmp/stale.log"),),
        proposal_hash="abc123",
    )
    writer.write_proposal(ctx, "cleanup-scratch", manifest)

    proposal_path = ctx.trail_dir / "proposals" / "cleanup-scratch.md"
    assert proposal_path.exists(), "proposals/<task_id>.md must exist"

    content = proposal_path.read_text(encoding="utf-8")
    assert "cleanup-scratch" in content
    assert "abc123" in content   # proposal_hash

    # task_events updated
    assert any(tid == "cleanup-scratch" for tid, _ in ctx.task_events), (
        "task_events must contain the task_id after write_proposal"
    )


# ---------------------------------------------------------------------------
# (e) write_applied_row emits applied/<task_id>.tsv
# ---------------------------------------------------------------------------


def test_evidence_writer_applied_tsv(tmp_path: Path) -> None:
    """write_applied_row creates applied/<task_id>.tsv with correct TSV columns."""
    config = _make_engine_config(tmp_path)
    writer = EvidenceWriter()
    ctx = _open_trail(writer, config.evidence_dir)

    result = {
        "moved": [("/tmp/old.log", "/archive/old.log")],
        "deleted": ["/tmp/empty.log"],
        "created": [],
        "dry_run_only": False,
    }
    writer.write_applied_row(ctx, "archive-logs", result)

    applied_path = ctx.trail_dir / "applied" / "archive-logs.tsv"
    assert applied_path.exists(), "applied/<task_id>.tsv must exist"

    content = applied_path.read_text(encoding="utf-8")
    assert "MOVE" in content
    assert "DELETE" in content
    assert "/tmp/old.log" in content
    assert "/tmp/empty.log" in content

    # task_events updated
    assert any(tid == "archive-logs" for tid, _ in ctx.task_events)


# ---------------------------------------------------------------------------
# (f) write_summary produces SUMMARY.md with guard + task summaries
# ---------------------------------------------------------------------------


def test_evidence_writer_summary_md(tmp_path: Path) -> None:
    """write_summary creates SUMMARY.md containing guard and task sections."""
    config = _make_engine_config(tmp_path)
    writer = EvidenceWriter()
    ctx = _open_trail(writer, config.evidence_dir)

    # Populate context with events
    ctx.guard_events.append(("disk_space", True, "ok"))
    ctx.task_events.append(("cleanup-scratch", "proposal_written"))
    ctx.task_events.append(("cleanup-scratch", "applied"))

    summary_path = writer.write_summary(ctx)

    assert summary_path.exists(), "SUMMARY.md must exist after write_summary"
    assert summary_path.name == "SUMMARY.md"

    content = summary_path.read_text(encoding="utf-8")
    assert "disk_space" in content, "SUMMARY.md must list guard events"
    assert "cleanup-scratch" in content, "SUMMARY.md must list task events"
    assert "PASS" in content, "SUMMARY.md must show guard PASS status"


# ---------------------------------------------------------------------------
# (g) write_exit_code writes exit_code file
# ---------------------------------------------------------------------------


def test_evidence_writer_exit_code(tmp_path: Path) -> None:
    """write_exit_code writes the integer exit code to exit_code file."""
    config = _make_engine_config(tmp_path)
    writer = EvidenceWriter()
    ctx = _open_trail(writer, config.evidence_dir)

    writer.write_exit_code(ctx, 0)

    exit_code_path = ctx.trail_dir / "exit_code"
    assert exit_code_path.exists(), "exit_code file must exist after write_exit_code"
    content = exit_code_path.read_text(encoding="utf-8").strip()
    assert content == "0", f"exit_code file must contain '0'; got {content!r}"
    assert ctx._exit_code == 0, "ctx._exit_code must be set to 0"


# ---------------------------------------------------------------------------
# (h) write_proposal_diff emits proposals/<task_id>.diff (SEV-3b)
# ---------------------------------------------------------------------------


def test_evidence_writer_proposal_diff(tmp_path: Path) -> None:
    """write_proposal_diff creates proposals/<task_id>.diff with the diff text."""
    config = _make_engine_config(tmp_path)
    writer = EvidenceWriter()
    ctx = _open_trail(writer, config.evidence_dir)

    diff_text = "--- a/old.log\n+++ b/new.log\n@@ -1 +1 @@\n-old\n+new\n"
    writer.write_proposal_diff(ctx, "diff-task", diff_text)

    diff_path = ctx.trail_dir / "proposals" / "diff-task.diff"
    assert diff_path.exists(), "proposals/<task_id>.diff must exist (SEV-3b)"
    content = diff_path.read_text(encoding="utf-8")
    assert content == diff_text, "diff content must match what was passed"


# ---------------------------------------------------------------------------
# (i) write_rollback_recipe emits applied/<task_id>.rollback.json
# ---------------------------------------------------------------------------


def test_evidence_writer_rollback_recipe(tmp_path: Path) -> None:
    """write_rollback_recipe creates applied/<task_id>.rollback.json."""
    config = _make_engine_config(tmp_path)
    writer = EvidenceWriter()
    ctx = _open_trail(writer, config.evidence_dir)

    recipe = {
        "task_id": "archive-logs",
        "undo_moves": [["/archive/old.log", "/tmp/old.log"]],
        "undo_deletes": [],
    }
    writer.write_rollback_recipe(ctx, "archive-logs", recipe)

    rollback_path = ctx.trail_dir / "applied" / "archive-logs.rollback.json"
    assert rollback_path.exists(), "applied/<task_id>.rollback.json must exist"

    data = json.loads(rollback_path.read_text(encoding="utf-8"))
    assert data["task_id"] == "archive-logs"
    assert "undo_moves" in data


# ---------------------------------------------------------------------------
# (j) Validator FORBIDDEN_PATH → refuse_fatal → errors.tsv written at evidence_dir root
# ---------------------------------------------------------------------------


def test_validator_forbidden_path_leads_to_errors_tsv(tmp_path: Path) -> None:
    """
    When validate_action returns FORBIDDEN_PATH and the caller invokes
    refuse_fatal (Path A), errors.tsv must be written to evidence_dir.

    This tests the A-path integration:
      validator → caller detects FORBIDDEN_PATH → refuse_fatal → errors.tsv
    """
    config = _make_engine_config(tmp_path)
    ctx = _make_tick_ctx(config)

    # Pick a path known to be FORBIDDEN_PATH (state DB)
    db_path = tmp_path / "state" / "app.db"
    validator = ActionValidator()
    verdict = validator.validate_action(db_path, Operation.DELETE)
    assert verdict == ValidatorResult.FORBIDDEN_PATH, (
        f"Expected FORBIDDEN_PATH for state DB; got {verdict.name}"
    )

    # Caller responds to FORBIDDEN_PATH by invoking refuse_fatal (Path A)
    with patch.object(sys, "exit") as mock_exit:
        refuse_fatal(
            RefusalReason.FORBIDDEN_PATH_VIOLATION,
            ctx,
            f"Forbidden path: {db_path}",
        )

    errors_tsv = config.evidence_dir / "errors.tsv"
    assert errors_tsv.exists(), (
        "errors.tsv must exist at evidence_dir root after refuse_fatal (Path A)"
    )
    content = errors_tsv.read_text(encoding="utf-8")
    assert "FORBIDDEN_PATH_VIOLATION" in content
    assert ctx.run_id in content

    # Verify quarantine file NOT written (Path A: no write_self_quarantine)
    quarantine_file = config.state_dir / "self_quarantine"
    assert not quarantine_file.exists(), (
        "Path A (refuse_fatal) must NOT write quarantine file — that is Path B only"
    )

    mock_exit.assert_called_once()
