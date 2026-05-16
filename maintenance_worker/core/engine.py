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
  _enumerate_candidates: wired to TaskRegistry (P5.3). Returns list[TaskCatalogEntry].
    For each entry, _dispatch_enumerate calls handler.enumerate(entry, ctx) to
    collect list[Candidate] per task. Handlers that are not yet implemented
    return an empty list (safe default).

run_tick is available as a module-level function (alias for
MaintenanceEngine.run_tick) to satisfy the smoke test:
  python3 -c "from maintenance_worker.core.engine import run_tick; ..."

Stdlib only. Imports only from maintenance_worker.types.* and
maintenance_worker.core.* (guards, refusal, kill_switch).
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from maintenance_worker.core.guards import (
    SEVERITY_REFUSE_FATAL,
    SEVERITY_SKIP_TICK,
    GuardReport,
    evaluate_all,
)
from maintenance_worker.core.install_metadata import (
    DryRunFloor,
    InstallMetadata,
    enforce_dry_run_floor,
    read_install_metadata,
)
from maintenance_worker.core.kill_switch import (
    check_scheduler_invocation,
    post_mutation_detector,
)
from maintenance_worker.core.refusal import refuse_fatal, skip_tick
from maintenance_worker.rules.parser import TaskCatalogEntry
from maintenance_worker.rules.task_registry import TaskRegistry
from maintenance_worker.types.candidates import Candidate
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
# Exceptions
# ---------------------------------------------------------------------------


class TaskHandlerNotFoundError(ModuleNotFoundError):
    """
    Raised by _dispatch_by_task_id when no handler module exists for task_id.

    Callers treat this as a safe fallback — engine returns dry_run_only=True
    rather than failing the tick. Handlers are optional until implemented.
    """


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
        # _enumerate_candidates returns list[TaskCatalogEntry] from catalog.
        # For each entry, dispatch to handler.enumerate(entry, ctx) to collect
        # list[Candidate] per task. Handlers not yet implemented return [].
        entries: list[TaskCatalogEntry] = self._enumerate_candidates(config, schedule="daily")
        # Weekly cadence gate: dispatch schedule="weekly" tasks only when due (≥7d since last run).
        # Cadence state persisted in state/maintenance_state/last_weekly_tick.json (atomic write).
        # On first run (file absent) weekly tasks are dispatched immediately.
        entries.extend(self._run_weekly_if_due(config))
        per_task_candidates: dict[str, list[Candidate]] = {}
        for entry in entries:
            task_candidates = self._dispatch_enumerate(entry, ctx)
            per_task_candidates[entry.spec.task_id] = task_candidates
        total_candidates = sum(len(v) for v in per_task_candidates.values())
        result.state_machine_breadcrumbs.append(("ENUMERATE_CANDIDATES", True))
        logger.info(
            "ENUMERATE_CANDIDATES: %d tasks, %d candidates total",
            len(entries),
            total_candidates,
        )

        # ── Phase 4: DRY_RUN_PROPOSAL ──────────────────────────────────────
        # Build ProposalManifest from per-task Candidates.
        # P5.5 evidence_writer will emit the full manifests to disk.
        manifests: dict[str, ProposalManifest] = {}
        for entry in entries:
            task_id = entry.spec.task_id
            task_cands = per_task_candidates.get(task_id, [])
            manifest = self._emit_dry_run_proposal(entry, task_cands)
            manifests[task_id] = manifest
        result.state_machine_breadcrumbs.append(("DRY_RUN_PROPOSAL", True))
        logger.info("DRY_RUN_PROPOSAL: %d manifests emitted (P5.5 stub)", len(manifests))

        # ── Phase 5: APPLY_DECISIONS ────────────────────────────────────────
        # Stages filesystem mutations only. No git commit or PR (P5.5 boundary).
        # MANUAL_CLI invocation mode forces dry_run_only regardless of live_default.
        # F2: read install_metadata once per tick; used by dry-run floor gate inside
        # _apply_decisions. None if absent (engine started before install script ran).
        install_meta: Optional[InstallMetadata] = None
        try:
            install_meta = read_install_metadata(config.state_dir)
        except (FileNotFoundError, Exception):
            pass  # absent or unreadable — floor gate skipped (safe: defaults to dry_run_only)

        force_dry_run = invocation_mode == InvocationMode.MANUAL_CLI
        apply_results: list[ApplyResult] = []
        for entry in entries:
            task_id = entry.spec.task_id
            task_cands = per_task_candidates.get(task_id, [])
            manifest = manifests.get(task_id, ProposalManifest(task_id=task_id))
            if not task_cands:
                # No candidates from enumerate → dry_run_only, no apply call
                apply_results.append(ApplyResult(task_id=task_id, dry_run_only=True))
                continue
            for candidate in task_cands:
                apply_result = self._apply_decisions(
                    entry, candidate, ctx,
                    force_dry_run=force_dry_run,
                    install_meta=install_meta,
                    proposal=manifest,
                )
                apply_results.append(apply_result)
        result.apply_results = apply_results
        result.state_machine_breadcrumbs.append(("APPLY_DECISIONS", True))
        logger.info("APPLY_DECISIONS: %d results across %d tasks", len(apply_results), len(entries))

        # ── Phase 5b: POST_DETECT (post-mutation detector) ─────────────────
        # Runs after APPLY_DECISIONS to catch any forbidden-path disk divergence.
        # Path B: divergence → write_self_quarantine + sys.exit(50).
        for entry in entries:
            task_id = entry.spec.task_id
            manifest = manifests.get(task_id, ProposalManifest(task_id=task_id))
            for apply_result in apply_results:
                if apply_result.task_id == task_id and not apply_result.dry_run_only:
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
            len(entries),
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

    _WEEKLY_CADENCE_DAYS = 7
    _WEEKLY_STATE_SUBDIR = "maintenance_state"
    _WEEKLY_STATE_FILE = "last_weekly_tick.json"

    def _run_weekly_if_due(self, config: EngineConfig) -> list[TaskCatalogEntry]:
        """
        Return weekly TaskCatalogEntries if ≥7 days have passed since the last
        weekly dispatch, then atomically update the cadence timestamp.

        Cadence state: state_dir/maintenance_state/last_weekly_tick.json
          {"last_run_ts": <unix float>}

        Absent file → first run → weekly is due.
        Atomic write: tmp file + os.replace() per Zeus convention.

        Returns [] if not yet due; returns weekly entries (may be empty if
        catalog has no weekly tasks) if due.
        """
        state_file = (
            config.state_dir / self._WEEKLY_STATE_SUBDIR / self._WEEKLY_STATE_FILE
        )
        now_ts = time.time()
        due = False

        if not state_file.exists():
            due = True
            logger.info(
                "_run_weekly_if_due: no cadence file found — weekly dispatch due (first run)"
            )
        else:
            try:
                data = json.loads(state_file.read_text())
                last_ts: float = float(data.get("last_run_ts", 0))
                elapsed_days = (now_ts - last_ts) / 86400
                if elapsed_days >= self._WEEKLY_CADENCE_DAYS:
                    due = True
                    logger.info(
                        "_run_weekly_if_due: %.1f days since last weekly run — dispatching",
                        elapsed_days,
                    )
                else:
                    logger.debug(
                        "_run_weekly_if_due: %.1f days since last weekly run — not yet due",
                        elapsed_days,
                    )
            except Exception as exc:
                logger.warning(
                    "_run_weekly_if_due: could not read cadence file %s: %s — skipping weekly",
                    state_file,
                    exc,
                )
                return []

        if not due:
            return []

        # Verify catalog exists before committing to a weekly dispatch.
        # If catalog is absent, _enumerate_candidates returns [] anyway; skip
        # the atomic state write so the cadence re-fires once the catalog appears.
        if not config.task_catalog_path.exists():
            logger.debug(
                "_run_weekly_if_due: catalog missing — skipping weekly dispatch"
            )
            return []

        # Atomic timestamp update (tmp + os.replace per Zeus convention)
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = state_file.with_suffix(".json.tmp")
            tmp_file.write_text(json.dumps({"last_run_ts": now_ts}))
            os.replace(str(tmp_file), str(state_file))
        except Exception as exc:
            logger.warning(
                "_run_weekly_if_due: could not update cadence file %s: %s",
                state_file,
                exc,
            )
            # Proceed with dispatch even if state write fails — missing write
            # means next tick re-dispatches weekly (safe: all weekly tasks are dry_run).

        return self._enumerate_candidates(config, schedule="weekly")

    def _enumerate_candidates(
        self, config: EngineConfig, schedule: str = "daily"
    ) -> list[TaskCatalogEntry]:
        """
        Enumerate task candidates from the task catalog.

        Loads TaskRegistry from config.task_catalog_path and returns all
        entries scheduled for the given schedule value. Defaults to "daily".
        Pass schedule="weekly" to surface weekly tasks (e.g. authority_drift_surface).

        run_tick passes schedule="daily"; future weekly dispatch will pass
        schedule="weekly" when the authority_drift_surface handler is implemented.
        """
        if not config.task_catalog_path.exists():
            logger.warning(
                "_enumerate_candidates: task_catalog_path missing: %s",
                config.task_catalog_path,
            )
            return []
        try:
            registry = TaskRegistry.from_catalog(config.task_catalog_path)
        except Exception as exc:
            logger.error(
                "_enumerate_candidates: failed to load catalog %s: %s",
                config.task_catalog_path,
                exc,
            )
            return []
        return registry.get_tasks_for_schedule(schedule)

    def _dispatch_by_task_id(self, task_id: str, method: str, *args: Any) -> Any:
        """
        Dispatch to a per-task rule module by task_id.

        Imports maintenance_worker.rules.<task_id> on first call (lazy).
        Raises TaskHandlerNotFoundError if the module does not exist.
        Raises AttributeError if the module lacks the requested method.

        method must be one of: "enumerate", "apply".
        """
        module_name = f"maintenance_worker.rules.{task_id}"
        try:
            mod = importlib.import_module(module_name)
        except ModuleNotFoundError:
            raise TaskHandlerNotFoundError(
                f"No handler module for task_id '{task_id}': "
                f"expected {module_name}.py to exist."
            ) from None
        handler = getattr(mod, method, None)
        if handler is None:
            raise AttributeError(
                f"Handler module {module_name} has no '{method}' function."
            )
        return handler(*args)

    def _dispatch_enumerate(
        self, entry: TaskCatalogEntry, ctx: TickContext
    ) -> list[Candidate]:
        """
        Call handler.enumerate(entry, ctx) for one catalog entry.

        Returns list[Candidate] from the handler. Falls back to [] if the
        handler module is not yet implemented (safe default — never skips
        known handlers). AttributeError propagates (handler exists but
        lacks enumerate → implementation bug, not missing module).
        """
        try:
            result: list[Candidate] = self._dispatch_by_task_id(
                entry.spec.task_id, "enumerate", entry, ctx
            )
            return result
        except TaskHandlerNotFoundError:
            logger.debug(
                "_dispatch_enumerate: no handler for %s; returning []",
                entry.spec.task_id,
            )
            return []
        except Exception as exc:
            logger.error(
                "_dispatch_enumerate: handler %s raised %s: %s; isolating from peers",
                entry.spec.task_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return []

    def _emit_dry_run_proposal(
        self, entry: TaskCatalogEntry, candidates: list[Candidate]
    ) -> ProposalManifest:
        """
        STUB: emit dry-run proposal manifest for one task.

        Receives the Candidates collected by _dispatch_enumerate. P5.5
        evidence_writer will write the full manifest to
        evidence_trail/<date>/proposals/<task_id>.md. Returns an
        empty ProposalManifest until P5.5 is delivered.
        """
        # P5.5 will implement: call EvidenceWriter.write_proposal(), hash manifest.
        return ProposalManifest(task_id=entry.spec.task_id)

    def _apply_decisions(
        self,
        entry: TaskCatalogEntry,
        candidate: Candidate,
        ctx: TickContext,
        force_dry_run: bool = False,
        install_meta: Optional[InstallMetadata] = None,
        proposal: Optional[ProposalManifest] = None,
    ) -> ApplyResult:
        """
        Stage filesystem mutations for one Candidate.

        Receives a single Candidate (from handler.enumerate). Returns
        ApplyResult with the staged diff. Does NOT commit to git or open
        PRs — P5.5 ApplyPublisher.publish() owns those steps (SCAFFOLD §3.5).

        If force_dry_run=True (MANUAL_CLI invocation), dry-run floor not yet
        met (< 30 days since install_metadata.first_run_at and task is not
        floor-exempt), or task has no ack, returns dry_run_only=True.

        F2: enforce_dry_run_floor gate is wired here so future packets cannot
        bypass it. After the gate, dispatches handler.apply(candidate, ctx)
        via _dispatch_by_task_id. Falls back to dry_run_only if handler
        is not found (safe default — never executes unknown handlers live).

        P5.5 GATE: when proposal manifest is the empty stub AND the handler is
        live-default + floor-exempt (i.e. capable of real mutations), force
        dry_run_only=True on the ctx passed to the handler. This prevents the
        live mutation → empty manifest → post_mutation_detector mismatch →
        SELF_QUARANTINE → exit(50) brick pattern. Remove this gate when P5.5
        _emit_dry_run_proposal() ships real manifest entries (Codex PR #124 P2).
        """
        task = entry.spec
        if force_dry_run:
            return ApplyResult(task_id=task.task_id, dry_run_only=True)

        # F2: dry-run floor gate. Enforced before any non-dry-run action.
        if install_meta is not None and not task.dry_run_floor_exempt:
            floor_result = enforce_dry_run_floor(
                task_id=task.task_id,
                install_meta=install_meta,
                floor_cfg=DryRunFloor(),
            )
            if floor_result == "ALLOWED_BUT_DRY_RUN_ONLY":
                return ApplyResult(task_id=task.task_id, dry_run_only=True)

        # P5.5 GATE: protect against live-mutation + empty-manifest → SELF_QUARANTINE brick.
        # _emit_dry_run_proposal() is a stub returning ProposalManifest with all-empty tuples.
        # post_mutation_detector compares ApplyResult.deleted/moved against manifest entries;
        # mismatch writes SELF_QUARANTINE + exit(50), bricking all future ticks permanently.
        # Condition: manifest is empty stub AND handler is live-default + floor-exempt.
        # Remove this gate when P5.5 evidence_writer populates real manifest entries.
        effective_ctx = ctx
        if proposal is not None:
            manifest_is_empty_stub = (
                not proposal.proposed_moves
                and not proposal.proposed_deletes
                and not proposal.proposed_creates
                and not proposal.proposed_modifies
            )
            task_is_live_exempt = (
                bool(entry.raw.get("live_default", False))
                and bool(task.dry_run_floor_exempt)
            )
            if manifest_is_empty_stub and task_is_live_exempt:
                effective_ctx = replace(ctx, dry_run_only=True)
                logger.warning(
                    "p5_5_dependency: forcing dry_run_only=True for task_id=%s "
                    "until _emit_dry_run_proposal lands (Codex PR #124 P2)",
                    task.task_id,
                )

        # Dispatch to per-task rule module. Falls back to dry_run_only if
        # the handler is not yet implemented (safe: never acts live without handler).
        try:
            result: ApplyResult = self._dispatch_by_task_id(
                task.task_id, "apply", candidate, effective_ctx
            )
            return result
        except TaskHandlerNotFoundError:
            logger.debug(
                "_apply_decisions: no handler for %s; returning dry_run_only",
                task.task_id,
            )
            return ApplyResult(task_id=task.task_id, dry_run_only=True)
        except Exception as exc:
            logger.error(
                "_apply_decisions: handler %s raised %s: %s; treating as dry_run",
                task.task_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
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
