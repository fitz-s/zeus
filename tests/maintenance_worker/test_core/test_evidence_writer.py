# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.3)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Evidence trail"
"""
Tests for maintenance_worker.core.evidence_writer — EvidenceWriter.

Coverage:
  - open_trail: creates per-date directory
  - write_config_snapshot: atomic write, JSON content, Path→str conversion
  - write_guards_tsv: TSV columns, pass/fail rows, accumulates guard_events
  - write_proposal: creates proposals/ subdir, markdown structure, task_events
  - write_proposal_diff: creates .diff file (SEV-3b)
  - write_applied_row: MOVE/DELETE/CREATE rows, applied/ subdir, task_events
  - write_rollback_recipe: JSON write, applied/ subdir
  - write_summary: SUMMARY.md content, no-work case, exit_code line
  - write_exit_code: integer file, updates ctx._exit_code
  - Atomic write: no partial files after successful write
  - Multiple tasks: separate files per task_id
  - TrailContext accumulation across multiple writes
  - Integration: full tick simulation (all methods in sequence)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from maintenance_worker.core.evidence_writer import EvidenceWriter, TrailContext
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import EngineConfig, ProposalManifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def writer() -> EvidenceWriter:
    return EvidenceWriter()


@pytest.fixture
def evidence_dir(tmp_path: Path) -> Path:
    d = tmp_path / "evidence"
    d.mkdir()
    return d


@pytest.fixture
def ctx(writer: EvidenceWriter, evidence_dir: Path) -> TrailContext:
    return writer.open_trail(date(2026, 5, 15), evidence_dir)


def make_config(tmp_path: Path) -> EngineConfig:
    """Build a minimal EngineConfig for snapshot tests."""
    return EngineConfig(
        repo_root=tmp_path / "repo",
        state_dir=tmp_path / "state",
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.md",
        live_default=False,
        scheduler="daily",
        notification_channel="discord",
        env_vars={},
    )


def make_proposal(task_id: str = "test_task") -> ProposalManifest:
    return ProposalManifest(
        task_id=task_id,
        proposed_moves=((Path("/a/src"), Path("/a/dst")),),
        proposed_deletes=(Path("/b/del"),),
        proposed_creates=(Path("/c/new"),),
        proposed_modifies=(Path("/d/mod"),),
        proposal_hash="abc123",
    )


def make_apply_result(task_id: str = "test_task") -> ApplyResult:
    return ApplyResult(
        task_id=task_id,
        moved=((Path("/a/src"), Path("/a/dst")),),
        deleted=(Path("/b/del"),),
        created=(Path("/c/new"),),
        requires_pr=False,
        dry_run_only=False,
    )


# ---------------------------------------------------------------------------
# open_trail
# ---------------------------------------------------------------------------


class TestOpenTrail:
    def test_creates_date_directory(self, writer: EvidenceWriter, evidence_dir: Path) -> None:
        ctx = writer.open_trail(date(2026, 5, 15), evidence_dir)
        assert ctx.trail_dir == evidence_dir / "2026-05-15"
        assert ctx.trail_dir.is_dir()

    def test_trail_dir_format(self, writer: EvidenceWriter, evidence_dir: Path) -> None:
        ctx = writer.open_trail(date(2026, 1, 3), evidence_dir)
        assert ctx.trail_dir.name == "2026-01-03"

    def test_created_at_is_utc(self, writer: EvidenceWriter, evidence_dir: Path) -> None:
        ctx = writer.open_trail(date(2026, 5, 15), evidence_dir)
        assert ctx.created_at.tzinfo is not None

    def test_open_trail_idempotent(self, writer: EvidenceWriter, evidence_dir: Path) -> None:
        """Calling open_trail twice for same date must not raise."""
        writer.open_trail(date(2026, 5, 15), evidence_dir)
        ctx2 = writer.open_trail(date(2026, 5, 15), evidence_dir)
        assert ctx2.trail_dir.is_dir()

    def test_task_events_initially_empty(self, writer: EvidenceWriter, evidence_dir: Path) -> None:
        ctx = writer.open_trail(date(2026, 5, 15), evidence_dir)
        assert ctx.task_events == []

    def test_guard_events_initially_empty(self, writer: EvidenceWriter, evidence_dir: Path) -> None:
        ctx = writer.open_trail(date(2026, 5, 15), evidence_dir)
        assert ctx.guard_events == []


# ---------------------------------------------------------------------------
# write_config_snapshot
# ---------------------------------------------------------------------------


class TestWriteConfigSnapshot:
    def test_creates_config_snapshot_json(
        self, writer: EvidenceWriter, ctx: TrailContext, tmp_path: Path
    ) -> None:
        config = make_config(tmp_path)
        writer.write_config_snapshot(ctx, config)
        target = ctx.trail_dir / "config_snapshot.json"
        assert target.exists()

    def test_content_is_valid_json(
        self, writer: EvidenceWriter, ctx: TrailContext, tmp_path: Path
    ) -> None:
        config = make_config(tmp_path)
        writer.write_config_snapshot(ctx, config)
        raw = (ctx.trail_dir / "config_snapshot.json").read_text()
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_paths_serialized_as_strings(
        self, writer: EvidenceWriter, ctx: TrailContext, tmp_path: Path
    ) -> None:
        config = make_config(tmp_path)
        writer.write_config_snapshot(ctx, config)
        data = json.loads((ctx.trail_dir / "config_snapshot.json").read_text())
        assert isinstance(data["repo_root"], str)
        assert isinstance(data["state_dir"], str)

    def test_live_default_preserved(
        self, writer: EvidenceWriter, ctx: TrailContext, tmp_path: Path
    ) -> None:
        config = make_config(tmp_path)
        writer.write_config_snapshot(ctx, config)
        data = json.loads((ctx.trail_dir / "config_snapshot.json").read_text())
        assert data["live_default"] is False

    def test_dict_config_accepted(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_config_snapshot(ctx, {"key": "value", "num": 42})
        data = json.loads((ctx.trail_dir / "config_snapshot.json").read_text())
        assert data["key"] == "value"

    def test_no_tmp_file_left_behind(
        self, writer: EvidenceWriter, ctx: TrailContext, tmp_path: Path
    ) -> None:
        writer.write_config_snapshot(ctx, make_config(tmp_path))
        tmp_files = list(ctx.trail_dir.glob("*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# write_guards_tsv
# ---------------------------------------------------------------------------


class TestWriteGuardsTsv:
    def _guard_row(
        self, name: str, ok: bool, reason: str = "", details: dict | None = None
    ) -> dict:
        return {
            "guard_name": name,
            "ok": ok,
            "reason": reason,
            "details": details or {},
        }

    def test_creates_guards_tsv(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_guards_tsv(ctx, [self._guard_row("g1", True)])
        assert (ctx.trail_dir / "guards.tsv").exists()

    def test_tsv_header(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_guards_tsv(ctx, [])
        lines = (ctx.trail_dir / "guards.tsv").read_text().splitlines()
        assert lines[0] == "guard_name\tok\treason\tdetails_json"

    def test_pass_row(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_guards_tsv(ctx, [self._guard_row("kill_switch", True)])
        lines = (ctx.trail_dir / "guards.tsv").read_text().splitlines()
        assert "kill_switch" in lines[1]
        assert "TRUE" in lines[1]

    def test_fail_row_has_reason(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_guards_tsv(
            ctx, [self._guard_row("dirty_repo", False, reason="uncommitted files")]
        )
        content = (ctx.trail_dir / "guards.tsv").read_text()
        assert "uncommitted files" in content

    def test_accumulates_guard_events(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_guards_tsv(
            ctx,
            [
                self._guard_row("g1", True),
                self._guard_row("g2", False, reason="fail"),
            ],
        )
        assert len(ctx.guard_events) == 2
        assert ctx.guard_events[0] == ("g1", True, "")
        assert ctx.guard_events[1][1] is False

    def test_empty_report(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_guards_tsv(ctx, [])
        lines = (ctx.trail_dir / "guards.tsv").read_text().splitlines()
        assert len(lines) == 1  # header only


# ---------------------------------------------------------------------------
# write_proposal
# ---------------------------------------------------------------------------


class TestWriteProposal:
    def test_creates_proposals_dir(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_proposal(ctx, "task_a", make_proposal("task_a"))
        assert (ctx.trail_dir / "proposals").is_dir()

    def test_creates_task_md_file(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_proposal(ctx, "task_a", make_proposal("task_a"))
        assert (ctx.trail_dir / "proposals" / "task_a.md").exists()

    def test_proposal_markdown_contains_task_id(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_proposal(ctx, "task_a", make_proposal("task_a"))
        content = (ctx.trail_dir / "proposals" / "task_a.md").read_text()
        assert "task_a" in content

    def test_proposal_markdown_contains_hash(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_proposal(ctx, "task_a", make_proposal("task_a"))
        content = (ctx.trail_dir / "proposals" / "task_a.md").read_text()
        assert "abc123" in content

    def test_proposal_contains_moves(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_proposal(ctx, "t1", make_proposal("t1"))
        content = (ctx.trail_dir / "proposals" / "t1.md").read_text()
        assert "src" in content
        assert "dst" in content

    def test_proposal_contains_deletes(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_proposal(ctx, "t1", make_proposal("t1"))
        content = (ctx.trail_dir / "proposals" / "t1.md").read_text()
        assert "del" in content

    def test_accumulates_task_event(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_proposal(ctx, "task_a", make_proposal("task_a"))
        assert any(tid == "task_a" for tid, _ in ctx.task_events)

    def test_empty_proposal(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        empty = ProposalManifest(task_id="empty_task")
        writer.write_proposal(ctx, "empty_task", empty)
        content = (ctx.trail_dir / "proposals" / "empty_task.md").read_text()
        assert "_(none)_" in content

    def test_separate_files_per_task(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_proposal(ctx, "task_a", make_proposal("task_a"))
        writer.write_proposal(ctx, "task_b", make_proposal("task_b"))
        assert (ctx.trail_dir / "proposals" / "task_a.md").exists()
        assert (ctx.trail_dir / "proposals" / "task_b.md").exists()


# ---------------------------------------------------------------------------
# write_proposal_diff (SEV-3b)
# ---------------------------------------------------------------------------


class TestWriteProposalDiff:
    def test_creates_diff_file(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_proposal_diff(ctx, "task_a", "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n")
        assert (ctx.trail_dir / "proposals" / "task_a.diff").exists()

    def test_diff_content_preserved(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        diff = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n"
        writer.write_proposal_diff(ctx, "task_a", diff)
        assert (ctx.trail_dir / "proposals" / "task_a.diff").read_text() == diff

    def test_empty_diff_accepted(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_proposal_diff(ctx, "task_a", "")
        assert (ctx.trail_dir / "proposals" / "task_a.diff").exists()

    def test_diff_and_md_coexist(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_proposal(ctx, "task_a", make_proposal("task_a"))
        writer.write_proposal_diff(ctx, "task_a", "diff content")
        assert (ctx.trail_dir / "proposals" / "task_a.md").exists()
        assert (ctx.trail_dir / "proposals" / "task_a.diff").exists()


# ---------------------------------------------------------------------------
# write_applied_row
# ---------------------------------------------------------------------------


class TestWriteAppliedRow:
    def test_creates_applied_dir(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_applied_row(ctx, "task_a", make_apply_result("task_a"))
        assert (ctx.trail_dir / "applied").is_dir()

    def test_creates_task_tsv(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_applied_row(ctx, "task_a", make_apply_result("task_a"))
        assert (ctx.trail_dir / "applied" / "task_a.tsv").exists()

    def test_tsv_header(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_applied_row(ctx, "task_a", make_apply_result("task_a"))
        lines = (ctx.trail_dir / "applied" / "task_a.tsv").read_text().splitlines()
        assert lines[0] == "kind\tpath_src\tpath_dst"

    def test_move_row_present(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_applied_row(ctx, "task_a", make_apply_result("task_a"))
        content = (ctx.trail_dir / "applied" / "task_a.tsv").read_text()
        assert "MOVE" in content

    def test_delete_row_present(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_applied_row(ctx, "task_a", make_apply_result("task_a"))
        content = (ctx.trail_dir / "applied" / "task_a.tsv").read_text()
        assert "DELETE" in content

    def test_create_row_present(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_applied_row(ctx, "task_a", make_apply_result("task_a"))
        content = (ctx.trail_dir / "applied" / "task_a.tsv").read_text()
        assert "CREATE" in content

    def test_accumulates_task_event(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_applied_row(ctx, "task_a", make_apply_result("task_a"))
        assert any(tid == "task_a" and ev == "applied" for tid, ev in ctx.task_events)

    def test_empty_apply_result(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        empty = ApplyResult(task_id="empty_task")
        writer.write_applied_row(ctx, "empty_task", empty)
        lines = (ctx.trail_dir / "applied" / "empty_task.tsv").read_text().splitlines()
        assert len(lines) == 1  # header only


# ---------------------------------------------------------------------------
# write_rollback_recipe
# ---------------------------------------------------------------------------


class TestWriteRollbackRecipe:
    def test_creates_rollback_json(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_rollback_recipe(ctx, "task_a", {"undo": ["git mv dst src"]})
        assert (ctx.trail_dir / "applied" / "task_a.rollback.json").exists()

    def test_content_is_valid_json(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_rollback_recipe(ctx, "task_a", {"steps": [1, 2, 3]})
        data = json.loads(
            (ctx.trail_dir / "applied" / "task_a.rollback.json").read_text()
        )
        assert data["steps"] == [1, 2, 3]

    def test_empty_dict_accepted(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_rollback_recipe(ctx, "task_a", {})
        data = json.loads(
            (ctx.trail_dir / "applied" / "task_a.rollback.json").read_text()
        )
        assert data == {}


# ---------------------------------------------------------------------------
# write_summary
# ---------------------------------------------------------------------------


class TestWriteSummary:
    def test_creates_summary_md(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        path = writer.write_summary(ctx)
        assert path == ctx.trail_dir / "SUMMARY.md"
        assert path.exists()

    def test_no_work_message_when_no_events(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_summary(ctx)
        content = (ctx.trail_dir / "SUMMARY.md").read_text()
        assert "no work done" in content

    def test_task_events_appear_in_summary(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_proposal(ctx, "task_a", make_proposal("task_a"))
        writer.write_summary(ctx)
        content = (ctx.trail_dir / "SUMMARY.md").read_text()
        assert "task_a" in content

    def test_guard_events_appear_in_summary(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_guards_tsv(
            ctx, [{"guard_name": "kill_switch", "ok": True, "reason": "", "details": {}}]
        )
        writer.write_summary(ctx)
        content = (ctx.trail_dir / "SUMMARY.md").read_text()
        assert "kill_switch" in content

    def test_exit_code_in_summary_when_set(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_exit_code(ctx, 0)
        writer.write_summary(ctx)
        content = (ctx.trail_dir / "SUMMARY.md").read_text()
        assert "exit_code" in content
        assert "0" in content

    def test_returns_path(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        path = writer.write_summary(ctx)
        assert isinstance(path, Path)


# ---------------------------------------------------------------------------
# write_exit_code
# ---------------------------------------------------------------------------


class TestWriteExitCode:
    def test_creates_exit_code_file(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_exit_code(ctx, 0)
        assert (ctx.trail_dir / "exit_code").exists()

    def test_exit_code_content(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_exit_code(ctx, 3)
        content = (ctx.trail_dir / "exit_code").read_text().strip()
        assert content == "3"

    def test_updates_ctx_exit_code(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        assert ctx._exit_code is None
        writer.write_exit_code(ctx, 0)
        assert ctx._exit_code == 0

    def test_nonzero_exit_code(self, writer: EvidenceWriter, ctx: TrailContext) -> None:
        writer.write_exit_code(ctx, 5)
        content = (ctx.trail_dir / "exit_code").read_text().strip()
        assert content == "5"


# ---------------------------------------------------------------------------
# Atomic write (no .tmp left behind)
# ---------------------------------------------------------------------------


class TestAtomicWrites:
    def test_no_tmp_files_after_proposal(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_proposal(ctx, "t1", make_proposal("t1"))
        tmp_files = list(ctx.trail_dir.rglob("*.tmp"))
        assert tmp_files == []

    def test_no_tmp_files_after_applied_row(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_applied_row(ctx, "t1", make_apply_result("t1"))
        tmp_files = list(ctx.trail_dir.rglob("*.tmp"))
        assert tmp_files == []

    def test_no_tmp_files_after_summary(
        self, writer: EvidenceWriter, ctx: TrailContext
    ) -> None:
        writer.write_summary(ctx)
        tmp_files = list(ctx.trail_dir.rglob("*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# Integration: full tick simulation
# ---------------------------------------------------------------------------


class TestFullTickSimulation:
    def test_full_tick_writes_all_artifacts(
        self, writer: EvidenceWriter, evidence_dir: Path, tmp_path: Path
    ) -> None:
        """Simulate a complete tick producing all evidence artifacts."""
        ctx = writer.open_trail(date(2026, 5, 15), evidence_dir)

        # Config snapshot
        writer.write_config_snapshot(ctx, make_config(tmp_path))
        assert (ctx.trail_dir / "config_snapshot.json").exists()

        # Guards
        writer.write_guards_tsv(
            ctx,
            [
                {"guard_name": "kill_switch", "ok": True, "reason": "", "details": {}},
                {"guard_name": "dirty_repo", "ok": True, "reason": "", "details": {}},
            ],
        )
        assert (ctx.trail_dir / "guards.tsv").exists()

        # Proposal + diff for task_a
        writer.write_proposal(ctx, "task_a", make_proposal("task_a"))
        writer.write_proposal_diff(ctx, "task_a", "--- a\n+++ b\n")

        # Applied row + rollback for task_a
        writer.write_applied_row(ctx, "task_a", make_apply_result("task_a"))
        writer.write_rollback_recipe(ctx, "task_a", {"undo": ["git mv"]})

        # Exit code + summary
        writer.write_exit_code(ctx, 0)
        summary_path = writer.write_summary(ctx)

        # Verify directory structure
        assert (ctx.trail_dir / "config_snapshot.json").exists()
        assert (ctx.trail_dir / "guards.tsv").exists()
        assert (ctx.trail_dir / "proposals" / "task_a.md").exists()
        assert (ctx.trail_dir / "proposals" / "task_a.diff").exists()
        assert (ctx.trail_dir / "applied" / "task_a.tsv").exists()
        assert (ctx.trail_dir / "applied" / "task_a.rollback.json").exists()
        assert (ctx.trail_dir / "exit_code").exists()
        assert summary_path.exists()

        # Verify no tmp files
        assert list(ctx.trail_dir.rglob("*.tmp")) == []

        # Verify summary references task_a and guards
        summary = summary_path.read_text()
        assert "task_a" in summary
        assert "kill_switch" in summary

    def test_multiple_tasks_produce_separate_files(
        self, writer: EvidenceWriter, evidence_dir: Path
    ) -> None:
        ctx = writer.open_trail(date(2026, 5, 15), evidence_dir)

        for task_id in ["task_alpha", "task_beta", "task_gamma"]:
            writer.write_proposal(ctx, task_id, ProposalManifest(task_id=task_id))
            writer.write_applied_row(ctx, task_id, ApplyResult(task_id=task_id))

        for task_id in ["task_alpha", "task_beta", "task_gamma"]:
            assert (ctx.trail_dir / "proposals" / f"{task_id}.md").exists()
            assert (ctx.trail_dir / "applied" / f"{task_id}.tsv").exists()

        # Six task_events (2 per task: proposal_written + applied)
        assert len(ctx.task_events) == 6
