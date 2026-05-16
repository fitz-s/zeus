# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/engine.py + §3.5
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Tick Lifecycle"
"""
engine — MaintenanceEngine and run_tick state machine.

State machine (SCAFFOLD §3 line ~144, DESIGN.md §"Tick Lifecycle"):
  START → LOAD_CONFIG → CHECK_GUARDS → ENUMERATE_CANDIDATES →
  DRY_RUN_PROPOSAL → APPLY_DECISIONS → SUMMARY_REPORT → END

All 7 transitions logged. Guard failure → refuse_fatal or skip_tick (never
silently continues). Zero `continue` statements in CHECK_GUARDS stage.

P5.1 ↔ P5.5 boundary (SCAFFOLD §3.5):
  _apply_decisions: stages filesystem diff → returns ApplyResult ONLY.
    Does NOT call git commit or gh pr create (P5.5 ApplyPublisher owns those).
  _emit_dry_run_proposal: STUB — returns empty ProposalManifest.
    Full publish (evidence_writer) deferred to P5.3/P5.5.
  _enumerate_candidates: STUB — returns empty list.
    Full enumeration (rules_parser, task_registry) deferred to P5.3.

run_tick is available as a module-level function (alias for
MaintenanceEngine.run_tick) to satisfy the smoke test:
  python3 -c "from maintenance_worker.core.engine import run_tick; ..."

Stdlib only. Imports only from maintenance_worker.types.* and
maintenance_worker.core.* (guards, refusal, kill_switch).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from maintenance_worker.core.guards import (
    SEVERITY_REFUSE_FATAL,
    SEVERITY_SKIP_TICK,
    GuardReport,
    evaluate_all,
)
from maintenance_worker.core.kill_switch import (
    check_scheduler_invocation,
    post_mutation_detector,
)
from maintenance_worker.core.refusal import refuse_fatal, skip_tick
from maintenance_worker.types.modes import InvocationMode, RefusalReason
from maintenance_worker.types.results import ApplyResult, CheckResult
from maintenance_worker.types.specs import (
    AckState,
    EngineConfig,
    ProposalManifest,
    TaskSpec,
    TickContext,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TickResult — returned by run_tick
# ---------------------------------------------------------------------------


@dataclass
class TickResult:
    """
    Result of a completed tick.

    state_machine_breadcrumbs: ordered list of (phase_name, ok) pairs
      recording each state machine transition. Useful for test assertions.
    guard_report: the GuardReport from CHECK_GUARDS phase.
    apply_results: list of ApplyResult from APPLY_DECISIONS (one per task).
    skipped: True if tick was soft-skipped (skip_tick path).
    run_id: UUID4 identifying this tick's evidence trail.
    summary_path: path to SUMMARY.md written in SUMMARY_REPORT phase, or None
      if not yet written (stubs return None; P5.3/P5.5 EvidenceWriter populates).
    """

    run_id: str
    started_at: datetime
    state_machine_breadcrumbs: list[tuple[str, bool]] = field(default_factory=list)
    guard_report: Optional[GuardReport] = None
    apply_results: list[ApplyResult] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    summary_path: Optional[Path] = None


# ---------------------------------------------------------------------------
# MaintenanceEngine
# ---------------------------------------------------------------------------


class MaintenanceEngine:
    """
    Maintenance engine — runs one tick of the state machine.

    Usage:
        engine = MaintenanceEngine()
        result = engine.run_tick(config)

    Or via module-level alias:
        from maintenance_worker.core.engine import run_tick
        result = run_tick(config)
    """

    def run_tick(self, config: EngineConfig) -> TickResult:
        """
        Execute one full tick through all 7 state machine phases.

        Exits non-zero (via refuse_fatal) on any hard guard failure.
        Returns normally on soft-skip or successful completion.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(tz=timezone.utc)

        # Build TickContext — used by refusal functions for logging + TSV writes
        invocation_mode = check_scheduler_invocation()
        ctx = TickContext(
            run_id=run_id,
            started_at=started_at,
            config=config,
            invocation_mode=invocation_mode,
        )

        result = TickResult(run_id=run_id, started_at=started_at)

        # ── Phase 0: START ──────────────────────────────────────────────────
        logger.info("START tick run_id=%s invocation_mode=%s", run_id, invocation_mode)
        result.state_machine_breadcrumbs.append(("START", True))

        # ── Phase 1: LOAD_CONFIG ────────────────────────────────────────────
        # Config is already resolved by caller (EngineConfig is the resolved form).
        # Fatal if required paths are missing.
        load_ok = self._validate_config(config)
        result.state_machine_breadcrumbs.append(("LOAD_CONFIG", load_ok))
        if not load_ok:
            logger.error("LOAD_CONFIG failed: invalid config paths")
            refuse_fatal(RefusalReason.CONFIG_INVALID, ctx, "Config validation failed")
            # refuse_fatal never returns (sys.exit); unreachable but satisfies type checker
            raise AssertionError("unreachable")  # pragma: no cover

        logger.info("LOAD_CONFIG ok repo_root=%s", config.repo_root)

        # ── Phase 2: CHECK_GUARDS ───────────────────────────────────────────
        # SCAFFOLD §6: zero `continue` inside CHECK_GUARDS. First failure exits
        # or skips tick — does NOT proceed to next guard.
        guard_report = evaluate_all(config.repo_root, config.state_dir)
        result.guard_report = guard_report
        result.state_machine_breadcrumbs.append(("CHECK_GUARDS", guard_report.all_passed))

        if not guard_report.all_passed:
            guard_name, failed_check = guard_report.first_failure
            severity = failed_check.details.get("severity", SEVERITY_REFUSE_FATAL)
            reason_str = failed_check.reason
            try:
                reason = RefusalReason(reason_str)
            except ValueError:
                reason = RefusalReason.KILL_SWITCH  # unknown → hard fail

            logger.warning(
                "CHECK_GUARDS failed: guard=%s reason=%s severity=%s",
                guard_name,
                reason_str,
                severity,
            )

            if severity == SEVERITY_SKIP_TICK:
                skip_tick(reason, ctx, failed_check.details.get("message", ""))
                result.skipped = True
                result.skip_reason = reason_str
                result.state_machine_breadcrumbs.append(("END", True))
                logger.info("END (skipped) run_id=%s reason=%s", run_id, reason_str)
                return result
            else:
                # Hard guard: refuse_fatal exits non-zero — no return
                refuse_fatal(reason, ctx, failed_check.details.get("message", ""))
                raise AssertionError("unreachable")  # pragma: no cover

        logger.info("CHECK_GUARDS passed: all 8 guards ok")

        # ── Phase 3: ENUMERATE_CANDIDATES ──────────────────────────────────
        # STUB: P5.3 task_registry + rules_parser will populate this.
        candidates: list[TaskSpec] = self._enumerate_candidates(config)
        result.state_machine_breadcrumbs.append(("ENUMERATE_CANDIDATES", True))
        logger.info("ENUMERATE_CANDIDATES: %d tasks (P5.3 stub)", len(candidates))

        # ── Phase 4: DRY_RUN_PROPOSAL ──────────────────────────────────────
        # STUB: P5.5 evidence_writer will emit the real proposal manifests.
        manifests: list[ProposalManifest] = []
        for task in candidates:
            manifest = self._emit_dry_run_proposal(task, [])
            manifests.append(manifest)
        result.state_machine_breadcrumbs.append(("DRY_RUN_PROPOSAL", True))
        logger.info("DRY_RUN_PROPOSAL: %d manifests emitted (P5.5 stub)", len(manifests))

        # ── Phase 5: APPLY_DECISIONS ────────────────────────────────────────
        # Stages filesystem mutations only. No git commit or PR (P5.5 boundary).
        # MANUAL_CLI invocation mode forces dry_run_only regardless of live_default.
        force_dry_run = invocation_mode == InvocationMode.MANUAL_CLI
        apply_results: list[ApplyResult] = []
        for task, manifest in zip(candidates, manifests):
            apply_result = self._apply_decisions(task, manifest, force_dry_run=force_dry_run)
            apply_results.append(apply_result)
        result.apply_results = apply_results
        result.state_machine_breadcrumbs.append(("APPLY_DECISIONS", True))
        logger.info("APPLY_DECISIONS: %d tasks processed", len(apply_results))

        # ── Phase 5b: POST_DETECT (post-mutation detector) ─────────────────
        # Runs after APPLY_DECISIONS to catch any forbidden-path disk divergence.
        # Path B: divergence → write_self_quarantine + sys.exit(50).
        for apply_result, manifest in zip(apply_results, manifests):
            if not apply_result.dry_run_only:
                post_mutation_detector(apply_result, manifest, config.state_dir)
        logger.info("POST_DETECT: all apply results verified against manifests")

        # ── Phase 6: SUMMARY_REPORT ─────────────────────────────────────────
        self._emit_summary(ctx, result)
        result.state_machine_breadcrumbs.append(("SUMMARY_REPORT", True))

        # ── Phase 7: END ─────────────────────────────────────────────────────
        result.state_machine_breadcrumbs.append(("END", True))
        logger.info(
            "END tick run_id=%s tasks=%d apply_results=%d",
            run_id,
            len(candidates),
            len(apply_results),
        )
        return result

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _validate_config(self, config: EngineConfig) -> bool:
        """
        Basic config validation. Returns False if required fields are invalid.

        Full schema validation (ConfigLoader.validate) deferred to P5.3.
        This guard only checks that the paths are non-empty Path objects.
        """
        required_paths = [config.repo_root, config.state_dir, config.evidence_dir]
        for p in required_paths:
            if not isinstance(p, Path) or str(p) in ("", "."):
                return False
        return True

    def _enumerate_candidates(self, config: EngineConfig) -> list[TaskSpec]:
        """
        STUB: enumerate task candidates from the task catalog.

        P5.3 task_registry.TaskRegistry.get_tasks_for_schedule() will
        implement this. Returns empty list until P5.3 is delivered.
        """
        # P5.3 will implement: load task_catalog_path, call TaskRegistry.load(),
        # filter by schedule, return active TaskSpec list.
        return []

    def _emit_dry_run_proposal(
        self, task: TaskSpec, candidates: list
    ) -> ProposalManifest:
        """
        STUB: emit dry-run proposal manifest for one task.

        P5.5 evidence_writer will write the full manifest to
        evidence_trail/<date>/proposals/<task_id>.md. Returns an
        empty ProposalManifest until P5.5 is delivered.
        """
        # P5.5 will implement: call EvidenceWriter.write_proposal(), hash manifest.
        return ProposalManifest(task_id=task.task_id)

    def _apply_decisions(
        self,
        task: TaskSpec,
        proposal: ProposalManifest,
        force_dry_run: bool = False,
    ) -> ApplyResult:
        """
        Stage filesystem mutations for one task.

        Returns ApplyResult with the staged diff. Does NOT commit to git
        or open PRs — P5.5 ApplyPublisher.publish() owns those steps
        (SCAFFOLD §3.5).

        If force_dry_run=True (MANUAL_CLI invocation) or task has no ack,
        returns dry_run_only=True with empty move/delete/create sets.

        Full apply logic (actual file moves, zero-byte deletes, stub creates)
        will be wired in P5.4 integration tests after P5.3 supplies real
        TaskSpec candidates with their AckState.
        """
        # P5.3/P5.4 will implement: AckManager.check_ack(), iterate proposed
        # moves/deletes, call os.replace/os.unlink on validated paths, record results.
        if force_dry_run:
            return ApplyResult(task_id=task.task_id, dry_run_only=True)

        # Without ack state (P5.3 not yet available), default to dry_run_only.
        return ApplyResult(task_id=task.task_id, dry_run_only=True)

    def _emit_summary(self, ctx: TickContext, result: TickResult) -> None:
        """
        Write SUMMARY.md to evidence_trail/<date>/.

        Writes a minimal SUMMARY.md to evidence_dir/<date>/SUMMARY.md and
        sets result.summary_path so cmd_run can pass it to notify_tick_summary.

        P5.3/P5.5 EvidenceWriter.write_summary() will replace this stub with
        the full artifact. The path contract (result.summary_path) is stable.
        """
        applied_count = sum(1 for r in result.apply_results if not r.dry_run_only)
        logger.info(
            "SUMMARY run_id=%s phases=%d applied=%d skipped=%s",
            ctx.run_id,
            len(result.state_machine_breadcrumbs),
            applied_count,
            result.skipped,
        )
        # Write minimal SUMMARY.md so notify_tick_summary has a path to read.
        try:
            today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            trail_dir = ctx.config.evidence_dir / today
            trail_dir.mkdir(parents=True, exist_ok=True)
            summary_path = trail_dir / "SUMMARY.md"
            content = (
                f"# Maintenance Worker Summary\n\n"
                f"run_id: {ctx.run_id}\n"
                f"phases: {len(result.state_machine_breadcrumbs)}\n"
                f"applied: {applied_count}\n"
                f"skipped: {result.skipped}\n"
            )
            tmp = summary_path.with_suffix(".md.tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(summary_path)
            result.summary_path = summary_path
        except OSError as exc:
            logger.warning("_emit_summary: could not write SUMMARY.md: %s", exc)


# ---------------------------------------------------------------------------
# Module-level alias — satisfies smoke test:
#   from maintenance_worker.core.engine import run_tick
# ---------------------------------------------------------------------------

_engine_singleton = MaintenanceEngine()


def run_tick(config: EngineConfig) -> TickResult:
    """
    Module-level entry point for one maintenance tick.

    Delegates to MaintenanceEngine().run_tick(config). Exists so callers
    can import run_tick directly without instantiating the engine.
    """
    return _engine_singleton.run_tick(config)
