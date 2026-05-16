# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.3)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Evidence trail"
"""
core/evidence_writer — EvidenceWriter class.

Owns all writes to the per-tick evidence trail directory. The trail enables
full decision reconstruction after the fact (SCAFFOLD §6 acceptance criterion).

Directory layout (DESIGN.md §"Evidence trail"):
  evidence_trail/<YYYY-MM-DD>/
    config_snapshot.json        # exact config used — written once per tick
    guards.tsv                  # per-guard pass/fail
    proposals/<task_id>.md      # per-task dry-run proposal
    proposals/<task_id>.diff    # unified diff per proposal (SEV-3b)
    applied/<task_id>.tsv       # per-task actions actually taken
    applied/<task_id>.commits   # SHA of any commit produced (P5.5)
    applied/<task_id>.pr        # URL of any PR opened (P5.5)
    errors.tsv                  # (caller-written; slot exists)
    SUMMARY.md                  # human-facing tick summary, ≤ 200 lines
    exit_code                   # final exit code integer

All writes are atomic: tmp file → os.replace() (matches install_metadata.py).
Subdirectories are created lazily on first write.

Public API:
  writer = EvidenceWriter()
  ctx    = writer.open_trail(date, evidence_dir)
  writer.write_config_snapshot(ctx, config)
  writer.write_guards_tsv(ctx, report)
  writer.write_proposal(ctx, task_id, proposal)
  writer.write_proposal_diff(ctx, task_id, diff_text)   # SEV-3b
  writer.write_applied_row(ctx, task_id, result)
  writer.write_rollback_recipe(ctx, task_id, recipe)
  path = writer.write_summary(ctx)
  writer.write_exit_code(ctx, code)

GuardReport and RollbackRecipe are forward-declared as protocols / dicts
to avoid circular imports from P5.4 modules not yet implemented.

Stdlib + json only. No PyYAML. No imports from maintenance_worker.core.engine.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# TrailContext
# ---------------------------------------------------------------------------


@dataclass
class TrailContext:
    """
    Mutable context for one tick's evidence trail.

    trail_dir: the per-date directory (evidence_dir / YYYY-MM-DD).
    created_at: UTC datetime the trail was opened.
    task_events: running list of (task_id, event_kind) for SUMMARY.md.
    guard_events: running list of (guard_name, ok, detail) for diagnostics.
    _exit_code: None until write_exit_code() is called.
    """

    trail_dir: Path
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    task_events: list[tuple[str, str]] = field(default_factory=list)
    guard_events: list[tuple[str, bool, str]] = field(default_factory=list)
    _exit_code: int | None = None


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    """
    Write text to path atomically via tmp+replace.

    Matches the pattern in install_metadata.py:
      tmp = target.with_suffix(".tmp")
      tmp.write_text(...)
      tmp.replace(target)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# EvidenceWriter
# ---------------------------------------------------------------------------


class EvidenceWriter:
    """
    Writes all artifacts for a per-tick evidence trail.

    Stateless: all state lives in TrailContext. Multiple EvidenceWriter
    instances may share a trail dir (P5.5 multi-phase write) but must not
    write the same artifact concurrently.
    """

    # ------------------------------------------------------------------
    # Trail construction
    # ------------------------------------------------------------------

    def open_trail(self, trail_date: date, evidence_dir: Path) -> TrailContext:
        """
        Create (or open existing) per-date evidence trail directory.

        trail_date: the tick's calendar date (UTC).
        evidence_dir: base directory for all trails (from EngineConfig).

        Returns a fresh TrailContext pointed at evidence_dir/<YYYY-MM-DD>/.
        """
        date_str = trail_date.strftime("%Y-%m-%d")
        trail_dir = evidence_dir / date_str
        trail_dir.mkdir(parents=True, exist_ok=True)
        return TrailContext(trail_dir=trail_dir)

    # ------------------------------------------------------------------
    # Config snapshot
    # ------------------------------------------------------------------

    def write_config_snapshot(self, ctx: TrailContext, config: Any) -> None:
        """
        Write config_snapshot.json to the trail directory.

        config: an EngineConfig (or any object with __dict__ / asdict).
        Serialized as a JSON object. Atomic write.

        Accepts EngineConfig (P5.0a frozen dataclass) or a plain dict
        to avoid import-time coupling with EngineConfig's Path fields.
        """
        from dataclasses import asdict, fields as dc_fields

        if isinstance(config, dict):
            payload = config
        else:
            # EngineConfig is a frozen dataclass; serialize via asdict
            # then convert Path → str for JSON serialisation
            try:
                raw = asdict(config)
            except TypeError:
                # Fallback: use __dict__ for non-dataclass objects
                raw = dict(vars(config))
            payload = _paths_to_str(raw)

        target = ctx.trail_dir / "config_snapshot.json"
        _atomic_write(target, json.dumps(payload, indent=2, default=str) + "\n")

    # ------------------------------------------------------------------
    # Guards TSV
    # ------------------------------------------------------------------

    def write_guards_tsv(self, ctx: TrailContext, report: Any) -> None:
        """
        Write guards.tsv to the trail directory.

        report: a GuardReport — either a list of CheckResult-like objects
                (with .ok, .reason, .details) or a list of dicts with
                keys ok, reason, details, guard_name.

        TSV columns: guard_name  ok  reason  details_json

        GuardReport is not yet a formal type (P5.4); this method accepts
        either a list of objects or a list of dicts.
        """
        lines = ["guard_name\tok\treason\tdetails_json"]
        for item in _iter_report(report):
            name = str(item.get("guard_name", ""))
            ok = str(item.get("ok", "")).upper()
            reason = str(item.get("reason", ""))
            details = json.dumps(item.get("details", {}), default=str)
            lines.append(f"{name}\t{ok}\t{reason}\t{details}")
            # Accumulate for SUMMARY.md
            ctx.guard_events.append((name, item.get("ok", False), reason))

        target = ctx.trail_dir / "guards.tsv"
        _atomic_write(target, "\n".join(lines) + "\n")

    # ------------------------------------------------------------------
    # Proposal
    # ------------------------------------------------------------------

    def write_proposal(
        self,
        ctx: TrailContext,
        task_id: str,
        proposal: Any,
    ) -> None:
        """
        Write proposals/<task_id>.md to the trail directory.

        proposal: a ProposalManifest (P5.0a frozen dataclass) or dict with
                  proposed_moves, proposed_deletes, proposed_creates,
                  proposed_modifies, proposal_hash.

        Emits a markdown document structured for human review.
        """
        manifest = _proposal_to_dict(proposal)

        lines = [
            f"# Proposal: {task_id}",
            "",
            f"**task_id**: `{task_id}`",
            f"**proposal_hash**: `{manifest.get('proposal_hash', '')}`",
            "",
            "## Proposed Moves",
        ]
        for src, dst in manifest.get("proposed_moves", []):
            lines.append(f"- `{src}` → `{dst}`")
        if not manifest.get("proposed_moves"):
            lines.append("_(none)_")

        lines += ["", "## Proposed Deletes"]
        for p in manifest.get("proposed_deletes", []):
            lines.append(f"- `{p}`")
        if not manifest.get("proposed_deletes"):
            lines.append("_(none)_")

        lines += ["", "## Proposed Creates"]
        for p in manifest.get("proposed_creates", []):
            lines.append(f"- `{p}`")
        if not manifest.get("proposed_creates"):
            lines.append("_(none)_")

        lines += ["", "## Proposed Modifies"]
        for p in manifest.get("proposed_modifies", []):
            lines.append(f"- `{p}`")
        if not manifest.get("proposed_modifies"):
            lines.append("_(none)_")

        proposals_dir = ctx.trail_dir / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        target = proposals_dir / f"{task_id}.md"
        _atomic_write(target, "\n".join(lines) + "\n")
        ctx.task_events.append((task_id, "proposal_written"))

    # ------------------------------------------------------------------
    # Proposal diff (SEV-3b)
    # ------------------------------------------------------------------

    def write_proposal_diff(
        self,
        ctx: TrailContext,
        task_id: str,
        diff_text: str,
    ) -> None:
        """
        Write proposals/<task_id>.diff to the trail directory.

        diff_text: unified diff string (generated by caller; this method
                   just writes it). Atomic write.

        SEV-3b: .diff surface per proposal — required by SCAFFOLD §3
        BATCH_DONE deviations list.
        """
        proposals_dir = ctx.trail_dir / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        target = proposals_dir / f"{task_id}.diff"
        _atomic_write(target, diff_text)

    # ------------------------------------------------------------------
    # Applied row
    # ------------------------------------------------------------------

    def write_applied_row(
        self,
        ctx: TrailContext,
        task_id: str,
        result: Any,
    ) -> None:
        """
        Write applied/<task_id>.tsv to the trail directory.

        result: an ApplyResult (P5.0a frozen dataclass) or dict with
                moved, deleted, created, requires_pr, dry_run_only.

        TSV columns: kind  path_src  path_dst

        kind values: MOVE, DELETE, CREATE
        For MOVE rows: path_src is source, path_dst is destination.
        For DELETE/CREATE: path_src is the path, path_dst is empty.
        """
        applied = _apply_result_to_dict(result)

        lines = ["kind\tpath_src\tpath_dst"]
        for src, dst in applied.get("moved", []):
            lines.append(f"MOVE\t{src}\t{dst}")
        for p in applied.get("deleted", []):
            lines.append(f"DELETE\t{p}\t")
        for p in applied.get("created", []):
            lines.append(f"CREATE\t{p}\t")

        applied_dir = ctx.trail_dir / "applied"
        applied_dir.mkdir(parents=True, exist_ok=True)
        target = applied_dir / f"{task_id}.tsv"
        _atomic_write(target, "\n".join(lines) + "\n")
        ctx.task_events.append((task_id, "applied"))

    # ------------------------------------------------------------------
    # Rollback recipe
    # ------------------------------------------------------------------

    def write_rollback_recipe(
        self,
        ctx: TrailContext,
        task_id: str,
        recipe: Any,
    ) -> None:
        """
        Write applied/<task_id>.rollback.json to the trail directory.

        recipe: a RollbackRecipe — a dict or any object serializable via
                json.dumps(default=str). RollbackRecipe is a P5.4-deferred
                type; accepted here as Any.

        The rollback recipe documents how to undo any applied mutations.
        """
        if isinstance(recipe, dict):
            payload = recipe
        else:
            try:
                from dataclasses import asdict
                payload = _paths_to_str(asdict(recipe))
            except (TypeError, AttributeError):
                payload = {"raw": str(recipe)}

        applied_dir = ctx.trail_dir / "applied"
        applied_dir.mkdir(parents=True, exist_ok=True)
        target = applied_dir / f"{task_id}.rollback.json"
        _atomic_write(target, json.dumps(payload, indent=2, default=str) + "\n")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def write_summary(self, ctx: TrailContext) -> Path:
        """
        Write SUMMARY.md to the trail directory.

        Returns the path written. SUMMARY.md is ≤ 200 lines per DESIGN.md.

        If the tick produced no task events, SUMMARY.md reports "no work done".
        """
        lines = [
            "# Maintenance Tick Summary",
            "",
            f"**trail_dir**: `{ctx.trail_dir}`",
            f"**created_at**: {ctx.created_at.isoformat()}",
            "",
        ]

        # Guard summary
        if ctx.guard_events:
            lines.append("## Guards")
            for name, ok, reason in ctx.guard_events:
                status = "PASS" if ok else "FAIL"
                detail = f" — {reason}" if reason else ""
                lines.append(f"- `{name}`: {status}{detail}")
            lines.append("")

        # Task summary
        lines.append("## Tasks")
        if ctx.task_events:
            seen: dict[str, list[str]] = {}
            for task_id, event_kind in ctx.task_events:
                seen.setdefault(task_id, []).append(event_kind)
            for task_id, events in seen.items():
                lines.append(f"- `{task_id}`: {', '.join(events)}")
        else:
            lines.append("_(no work done this tick)_")

        # Exit code
        if ctx._exit_code is not None:
            lines += ["", f"**exit_code**: {ctx._exit_code}"]

        target = ctx.trail_dir / "SUMMARY.md"
        _atomic_write(target, "\n".join(lines) + "\n")
        return target

    # ------------------------------------------------------------------
    # Exit code
    # ------------------------------------------------------------------

    def write_exit_code(self, ctx: TrailContext, code: int) -> None:
        """
        Write the final exit code integer to exit_code file.

        One integer on a single line. Atomic write.
        """
        ctx._exit_code = code
        target = ctx.trail_dir / "exit_code"
        _atomic_write(target, f"{code}\n")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _paths_to_str(obj: Any) -> Any:
    """Recursively convert Path objects to str for JSON serialisation."""
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _paths_to_str(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_paths_to_str(i) for i in obj]
    return obj


def _iter_report(report: Any) -> list[dict[str, Any]]:
    """
    Normalise a GuardReport to a list of dicts.

    Accepts:
      - list of dicts
      - list of objects with .ok, .reason, .details, optionally .guard_name
      - a single dict (wrapped)
      - None or empty (returns [])
    """
    if not report:
        return []
    if isinstance(report, dict):
        return [report]
    result: list[dict[str, Any]] = []
    for item in report:
        if isinstance(item, dict):
            result.append(item)
        else:
            result.append(
                {
                    "guard_name": getattr(item, "guard_name", ""),
                    "ok": getattr(item, "ok", False),
                    "reason": getattr(item, "reason", ""),
                    "details": getattr(item, "details", {}),
                }
            )
    return result


def _proposal_to_dict(proposal: Any) -> dict[str, Any]:
    """
    Normalise a ProposalManifest or dict to a plain dict.

    Converts tuple-of-tuples (frozen dataclass) to list-of-lists.
    """
    if isinstance(proposal, dict):
        return proposal
    try:
        from dataclasses import asdict
        raw = asdict(proposal)
        return raw
    except (TypeError, AttributeError):
        return {}


def _apply_result_to_dict(result: Any) -> dict[str, Any]:
    """
    Normalise an ApplyResult or dict to a plain dict.

    moved: list of [src, dst] pairs (str)
    deleted: list of str
    created: list of str
    """
    if isinstance(result, dict):
        return result
    try:
        from dataclasses import asdict
        raw = asdict(result)
        return raw
    except (TypeError, AttributeError):
        return {}
