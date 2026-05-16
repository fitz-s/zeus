# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/guards.py + §6
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Refusal Modes"
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Kill Switch"
"""
guards — 8 pre-tick guard check functions and GuardReport aggregator.

SCAFFOLD §6 ND3 split:
  6 refuse_fatal hard guards (CheckResult.ok=False → engine calls refuse_fatal):
    check_kill_switch, check_dirty_repo, check_active_rebase,
    check_disk_free, check_inflight_pr, check_self_quarantined

  2 skip_tick soft guards (CheckResult.ok=False → engine calls skip_tick):
    check_no_pause_flag, check_oncall_quiet

Guard severity is recorded in CheckResult.details["severity"] so the engine
can dispatch to the correct refusal function without hardcoding the split.

3 new guards (git_operation_guard, gh_operation_guard, subprocess_guard)
are P5.2 deliverables — not implemented here.

Stdlib only. Imports only from maintenance_worker.types.*.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from maintenance_worker.types.modes import RefusalReason
from maintenance_worker.types.results import CheckResult

logger = logging.getLogger(__name__)

# Severity labels stored in CheckResult.details for engine dispatch.
SEVERITY_REFUSE_FATAL = "refuse_fatal"
SEVERITY_SKIP_TICK = "skip_tick"

# Disk-free threshold default: refuse if free < 5% of total.
DEFAULT_DISK_FREE_THRESHOLD_PCT: float = 5.0


# ---------------------------------------------------------------------------
# GuardReport — aggregator returned by evaluate_all
# ---------------------------------------------------------------------------


@dataclass
class GuardReport:
    """
    Aggregated result of all guard checks for one tick.

    results: ordered list of (guard_name, CheckResult) pairs.
    all_passed: True only when every CheckResult.ok is True.
    first_failure: the first CheckResult with ok=False, or None.
    """

    results: list[tuple[str, CheckResult]] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.ok for _, r in self.results)

    @property
    def first_failure(self) -> Optional[tuple[str, CheckResult]]:
        for name, r in self.results:
            if not r.ok:
                return (name, r)
        return None


# ---------------------------------------------------------------------------
# Hard guards — refuse_fatal on failure (6 total)
# ---------------------------------------------------------------------------


def check_kill_switch(state_dir: Path) -> CheckResult:
    """
    Hard guard: KILL_SWITCH file present → refuse_fatal(KILL_SWITCH).

    Per SAFETY_CONTRACT.md §"Kill Switch": kill switch is sticky — the agent
    never auto-removes it. Human must delete to resume.
    """
    ks_file = state_dir / "KILL_SWITCH"
    if ks_file.exists():
        return CheckResult(
            ok=False,
            reason=RefusalReason.KILL_SWITCH.value,
            details={
                "severity": SEVERITY_REFUSE_FATAL,
                "file": str(ks_file),
                "message": "KILL_SWITCH file present; human must remove to resume.",
            },
        )
    return CheckResult(ok=True, reason="", details={"severity": SEVERITY_REFUSE_FATAL})


def check_dirty_repo(repo: Path) -> CheckResult:
    """
    Hard guard: repo has uncommitted changes → refuse_fatal(DIRTY_REPO).

    Uses `git status --porcelain` — any output means dirty.
    If git is not available or repo is not a git repo, returns ok=False
    (fail-safe: unknown state is treated as dirty).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return CheckResult(
                ok=False,
                reason=RefusalReason.DIRTY_REPO.value,
                details={
                    "severity": SEVERITY_REFUSE_FATAL,
                    "message": f"git status failed (returncode={result.returncode}): {result.stderr.strip()}",
                },
            )
        dirty = result.stdout.strip()
        if dirty:
            return CheckResult(
                ok=False,
                reason=RefusalReason.DIRTY_REPO.value,
                details={
                    "severity": SEVERITY_REFUSE_FATAL,
                    "dirty_lines": dirty,
                    "message": "Repo has uncommitted changes.",
                },
            )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            ok=False,
            reason=RefusalReason.DIRTY_REPO.value,
            details={
                "severity": SEVERITY_REFUSE_FATAL,
                "message": f"git not available or timed out: {exc}",
            },
        )
    return CheckResult(ok=True, reason="", details={"severity": SEVERITY_REFUSE_FATAL})


def check_active_rebase(repo: Path) -> CheckResult:
    """
    Hard guard: active rebase/merge/cherry-pick in progress → refuse_fatal(ACTIVE_REBASE).

    Detects by checking for interrupt-state directories/files in .git/:
      MERGE_HEAD, CHERRY_PICK_HEAD, REBASE_HEAD, rebase-merge/, rebase-apply/
    """
    git_dir = repo / ".git"
    interrupt_indicators = [
        git_dir / "MERGE_HEAD",
        git_dir / "CHERRY_PICK_HEAD",
        git_dir / "REBASE_HEAD",
        git_dir / "rebase-merge",
        git_dir / "rebase-apply",
    ]
    for indicator in interrupt_indicators:
        if indicator.exists():
            return CheckResult(
                ok=False,
                reason=RefusalReason.ACTIVE_REBASE.value,
                details={
                    "severity": SEVERITY_REFUSE_FATAL,
                    "indicator": str(indicator),
                    "message": f"Git interrupt state detected: {indicator.name}",
                },
            )
    return CheckResult(ok=True, reason="", details={"severity": SEVERITY_REFUSE_FATAL})


def check_disk_free(
    repo: Path,
    threshold_pct: float = DEFAULT_DISK_FREE_THRESHOLD_PCT,
) -> CheckResult:
    """
    Hard guard: disk free below threshold_pct → refuse_fatal(LOW_DISK).

    Uses shutil.disk_usage. Fails safe: if usage cannot be determined,
    returns ok=False (treat unknown as low).
    """
    try:
        usage = shutil.disk_usage(repo)
        free_pct = (usage.free / usage.total) * 100.0
        if free_pct < threshold_pct:
            return CheckResult(
                ok=False,
                reason=RefusalReason.LOW_DISK.value,
                details={
                    "severity": SEVERITY_REFUSE_FATAL,
                    "free_pct": round(free_pct, 2),
                    "threshold_pct": threshold_pct,
                    "message": f"Disk free {free_pct:.1f}% below threshold {threshold_pct}%.",
                },
            )
    except OSError as exc:
        return CheckResult(
            ok=False,
            reason=RefusalReason.LOW_DISK.value,
            details={
                "severity": SEVERITY_REFUSE_FATAL,
                "message": f"Cannot determine disk usage: {exc}",
            },
        )
    return CheckResult(ok=True, reason="", details={"severity": SEVERITY_REFUSE_FATAL})


def check_inflight_pr(state_dir: Path) -> CheckResult:
    """
    Hard guard: open maintenance PR in-flight → refuse_fatal(INFLIGHT_PR).

    Checks for a state file written by the engine after opening a maintenance
    PR. File: state_dir/INFLIGHT_MAINTENANCE_PR containing the PR URL.

    Full gh-based detection (gh pr list --label maintenance) deferred to P5.5
    CLI layer which has access to the gh context. This guard checks the local
    state file written when a PR was opened.
    """
    inflight_file = state_dir / "INFLIGHT_MAINTENANCE_PR"
    if inflight_file.exists():
        pr_url = inflight_file.read_text(encoding="utf-8").strip()
        return CheckResult(
            ok=False,
            reason=RefusalReason.INFLIGHT_PR.value,
            details={
                "severity": SEVERITY_REFUSE_FATAL,
                "pr_url": pr_url,
                "message": f"Inflight maintenance PR detected: {pr_url}",
            },
        )
    return CheckResult(ok=True, reason="", details={"severity": SEVERITY_REFUSE_FATAL})


def check_self_quarantined(state_dir: Path) -> CheckResult:
    """
    Hard guard: SELF_QUARANTINE file present → refuse_fatal(SELF_QUARANTINED).

    Written only by post_mutation_detector (Path B). Human must reconcile
    and delete to resume.
    """
    qfile = state_dir / "SELF_QUARANTINE"
    if qfile.exists():
        reason_text = ""
        try:
            reason_text = qfile.read_text(encoding="utf-8").strip()
        except OSError:
            reason_text = "(unreadable)"
        return CheckResult(
            ok=False,
            reason=RefusalReason.SELF_QUARANTINED.value,
            details={
                "severity": SEVERITY_REFUSE_FATAL,
                "file": str(qfile),
                "quarantine_content": reason_text,
                "message": "SELF_QUARANTINE present; human must reconcile and delete.",
            },
        )
    return CheckResult(ok=True, reason="", details={"severity": SEVERITY_REFUSE_FATAL})


# ---------------------------------------------------------------------------
# Soft guards — skip_tick on failure (2 total)
# ---------------------------------------------------------------------------


def check_no_pause_flag(state_dir: Path) -> CheckResult:
    """
    Soft guard: MAINTENANCE_PAUSED present → skip_tick(MAINTENANCE_PAUSED).

    Per SAFETY_CONTRACT.md lines 207-213: skips ticks without requiring
    re-acknowledge to resume. Useful for short freezes (deploys, incidents).
    """
    pause_file = state_dir / "MAINTENANCE_PAUSED"
    if pause_file.exists():
        return CheckResult(
            ok=False,
            reason=RefusalReason.MAINTENANCE_PAUSED.value,
            details={
                "severity": SEVERITY_SKIP_TICK,
                "file": str(pause_file),
                "message": "MAINTENANCE_PAUSED present; skipping tick, auto-resumes when removed.",
            },
        )
    return CheckResult(ok=True, reason="", details={"severity": SEVERITY_SKIP_TICK})


def check_oncall_quiet(state_dir: Path) -> CheckResult:
    """
    Soft guard: ONCALL_QUIET file present → skip_tick(ONCALL_QUIET).

    Per SCAFFOLD §6 ND3: oncall_quiet is skip_tick severity (not refuse_fatal).
    """
    quiet_file = state_dir / "ONCALL_QUIET"
    if quiet_file.exists():
        return CheckResult(
            ok=False,
            reason=RefusalReason.ONCALL_QUIET.value,
            details={
                "severity": SEVERITY_SKIP_TICK,
                "file": str(quiet_file),
                "message": "ONCALL_QUIET present; skipping tick until oncall window clears.",
            },
        )
    return CheckResult(ok=True, reason="", details={"severity": SEVERITY_SKIP_TICK})


# ---------------------------------------------------------------------------
# evaluate_all — runs all 8 guards in priority order
# ---------------------------------------------------------------------------


def evaluate_all(repo: Path, state_dir: Path) -> GuardReport:
    """
    Run all 8 guards and return a GuardReport.

    Order: hard guards first (fail fast on most critical), soft guards last.
    The engine iterates guard results in order and stops at first failure,
    calling refuse_fatal or skip_tick as appropriate.

    SCAFFOLD §6: zero `continue` statements inside CHECK_GUARDS stage —
    engine must exit or skip on first guard failure, not proceed to next task.
    """
    report = GuardReport()

    # Hard guards (6) — checked in priority order
    report.results.append(("check_kill_switch", check_kill_switch(state_dir)))
    report.results.append(("check_self_quarantined", check_self_quarantined(state_dir)))
    report.results.append(("check_dirty_repo", check_dirty_repo(repo)))
    report.results.append(("check_active_rebase", check_active_rebase(repo)))
    report.results.append(("check_disk_free", check_disk_free(repo)))
    report.results.append(("check_inflight_pr", check_inflight_pr(state_dir)))

    # Soft guards (2)
    report.results.append(("check_no_pause_flag", check_no_pause_flag(state_dir)))
    report.results.append(("check_oncall_quiet", check_oncall_quiet(state_dir)))

    return report
