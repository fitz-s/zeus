# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §authority_drift_surface
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/03_authority_drift_remediation/REMEDIATION_PLAN.md
"""
Handler: authority_drift_surface

Surfaces drift between authority docs and code on a weekly schedule.

enumerate(): scans authority_docs dirs (architecture/, docs/operations/task_*/,
  docs/review/) for drift signals. Drift detection uses a heuristic scoring
  model:
    - File pair mismatch (authority .md modified more recently than sibling
      code file it documents): +0.2 per pair
    - Stale authority file (mtime > stale_threshold_days without any code
      update in the same path prefix): +0.1 per file
    - Escalation: drift_score >= escalate_threshold → DRIFT_ESCALATE verdict

  Outputs are surface-only (no edits to authority docs).
  schedule: weekly (schedule_day: monday) — run_tick currently hardcodes
  schedule="daily" (engine.py); weekly dispatch is deferred to WAVE 7.
  This handler will NOT be called on normal daily ticks; it is wired but
  remains dormant until the weekly dispatch is implemented.

apply(): always dry_run_only=True (live_default: false in catalog).
  Returns mock diff showing what would be written to output_only_dir.

Verdict strings:
  DRIFT_SURFACE_CANDIDATE   — doc/code mtime mismatch above drift_score_threshold
  DRIFT_ESCALATE            — drift_score >= escalate_threshold (high severity)
  SKIP_SCORE_BELOW_THRESHOLD — drift score below threshold
  SKIP_NO_CODE_SIBLING      — authority doc has no identifiable code sibling
  SKIP_TOO_FRESH            — both doc and code modified recently (< stale_days)

Deviation: schedule=weekly is not dispatched in the current engine
  (engine.py:216 hardcodes schedule="daily"). This handler is dormant
  until WAVE 7 implements weekly tick dispatch. TODO added at engine.py:216.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import TickContext

logger = logging.getLogger(__name__)

# Verdict strings
VERDICT_CANDIDATE = "DRIFT_SURFACE_CANDIDATE"
VERDICT_ESCALATE = "DRIFT_ESCALATE"
VERDICT_SKIP_BELOW = "SKIP_SCORE_BELOW_THRESHOLD"
VERDICT_SKIP_NO_SIBLING = "SKIP_NO_CODE_SIBLING"
VERDICT_SKIP_FRESH = "SKIP_TOO_FRESH"

DEFAULT_DRIFT_THRESHOLD = 0.3
DEFAULT_ESCALATE_THRESHOLD = 0.7
DEFAULT_STALE_DAYS = 30  # days without code update to count as stale

# Authority doc directories to scan (relative to repo_root)
DEFAULT_AUTHORITY_DIRS = [
    "architecture",
    "docs/operations",
    "docs/review",
]

# Code directories that authority docs map to
_CODE_DIRS = ["src", "tests", "maintenance_worker"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate(entry: Any, ctx: TickContext) -> list[Candidate]:  # noqa: A001
    """
    Surface drift between authority docs and code.

    Scans DEFAULT_AUTHORITY_DIRS for .md files and estimates drift score
    based on mtime gap between doc and code siblings.

    schedule: weekly — this handler is currently dormant because engine.py
    run_tick hardcodes schedule="daily". WAVE 7 will add weekly dispatch.
    """
    raw: dict = entry.raw
    config: dict = raw.get("config", {})
    drift_threshold: float = float(config.get("drift_score_threshold", DEFAULT_DRIFT_THRESHOLD))
    escalate_threshold: float = float(config.get("escalate_threshold", DEFAULT_ESCALATE_THRESHOLD))
    repo_root: Path = ctx.config.repo_root

    now_ts = time.time()
    stale_seconds = DEFAULT_STALE_DAYS * 86400

    candidates: list[Candidate] = []

    for dir_rel in DEFAULT_AUTHORITY_DIRS:
        authority_dir = repo_root / dir_rel
        if not authority_dir.exists():
            logger.debug("authority_drift_surface: authority_dir missing: %s", authority_dir)
            continue

        for doc_path in sorted(authority_dir.rglob("*.md")):
            if not doc_path.is_file():
                continue

            doc_mtime = _get_mtime(doc_path)
            if doc_mtime is None:
                continue

            # Find best code sibling for this authority doc
            code_sibling, code_mtime = _find_code_sibling(doc_path, repo_root)

            if code_sibling is None or code_mtime is None:
                candidates.append(Candidate(
                    task_id="authority_drift_surface",
                    path=doc_path,
                    verdict=VERDICT_SKIP_NO_SIBLING,
                    reason="no identifiable code sibling for authority doc",
                    evidence={"doc": str(doc_path.relative_to(repo_root))},
                ))
                continue

            # Skip if both doc and code are fresh (< stale_days)
            doc_age = now_ts - doc_mtime
            code_age = now_ts - code_mtime
            if doc_age < stale_seconds and code_age < stale_seconds:
                candidates.append(Candidate(
                    task_id="authority_drift_surface",
                    path=doc_path,
                    verdict=VERDICT_SKIP_FRESH,
                    reason=f"both doc and code recently updated (doc_age={round(doc_age/86400,1)}d, code_age={round(code_age/86400,1)}d)",
                    evidence={"doc_age_days": round(doc_age / 86400, 1), "code_age_days": round(code_age / 86400, 1)},
                ))
                continue

            # Compute drift score: doc newer than code = possible undocumented change
            drift_score = _compute_drift_score(doc_mtime, code_mtime, now_ts, stale_seconds)

            if drift_score < drift_threshold:
                candidates.append(Candidate(
                    task_id="authority_drift_surface",
                    path=doc_path,
                    verdict=VERDICT_SKIP_BELOW,
                    reason=f"drift score below threshold (score={drift_score:.2f} < {drift_threshold})",
                    evidence={"drift_score": drift_score, "threshold": drift_threshold},
                ))
                continue

            verdict = VERDICT_ESCALATE if drift_score >= escalate_threshold else VERDICT_CANDIDATE
            candidates.append(Candidate(
                task_id="authority_drift_surface",
                path=doc_path,
                verdict=verdict,
                reason=f"authority/code drift detected (score={drift_score:.2f})",
                evidence={
                    "drift_score": drift_score,
                    "doc": str(doc_path.relative_to(repo_root)),
                    "code_sibling": str(code_sibling.relative_to(repo_root)),
                    "doc_age_days": round(doc_age / 86400, 1),
                    "code_age_days": round(code_age / 86400, 1),
                },
            ))

    return candidates


def apply(decision: Candidate, ctx: TickContext) -> ApplyResult:
    """
    Surface drift report. Always dry_run_only=True (live_default: false).

    TOP-OF-FUNCTION GUARD — this task is ALWAYS surface-only.
    Returns mock diff showing what would be written to drift_surface dir.
    """
    # This task is ALWAYS surface-only — no authority doc edits permitted.
    mock = _mock_diff(decision)
    return ApplyResult(
        task_id="authority_drift_surface",
        dry_run_only=True,
        diff=mock,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_code_sibling(doc_path: Path, repo_root: Path) -> tuple[Path | None, float | None]:
    """
    Find the best code sibling for an authority doc.

    Strategy: look for a Python/shell file in _CODE_DIRS whose stem
    matches the doc stem (case-insensitive, normalizing - to _).
    Falls back to any file in the nearest code dir with a matching prefix.

    Returns (sibling_path, sibling_mtime) or (None, None).
    """
    doc_stem = doc_path.stem.lower().replace("-", "_").replace(" ", "_")

    for code_dir_name in _CODE_DIRS:
        code_dir = repo_root / code_dir_name
        if not code_dir.exists():
            continue

        for code_file in sorted(code_dir.rglob("*.py")):
            candidate_stem = code_file.stem.lower()
            if candidate_stem == doc_stem or doc_stem.startswith(candidate_stem[:8]):
                mtime = _get_mtime(code_file)
                if mtime is not None:
                    return code_file, mtime

    return None, None


def _compute_drift_score(doc_mtime: float, code_mtime: float, now_ts: float, stale_seconds: float) -> float:
    """
    Compute a drift score in [0.0, 1.0].

    Score is higher when:
    - Code is significantly older than doc (doc updated without code update)
    - Either file is very stale relative to stale_seconds

    Formula:
      base = abs(doc_mtime - code_mtime) / stale_seconds  (clamped to 1.0)
      stale_bonus = +0.1 if code is older than stale_seconds
      result = min(base + stale_bonus, 1.0)
    """
    mtime_gap = abs(doc_mtime - code_mtime)
    base = min(mtime_gap / stale_seconds, 1.0) if stale_seconds > 0 else 0.0

    code_age = now_ts - code_mtime
    stale_bonus = 0.1 if code_age > stale_seconds else 0.0

    return min(base + stale_bonus, 1.0)


def _get_mtime(path: Path) -> float | None:
    """Return mtime of path in seconds, or None if inaccessible."""
    try:
        return path.stat().st_mtime
    except (OSError, FileNotFoundError):
        return None


def _mock_diff(decision: Candidate) -> tuple[str, ...]:
    """Return mock diff showing planned drift surface emission."""
    return (
        f"[DRY RUN] would write drift report for: {decision.path}",
        f"       → evidence/drift_surface/{decision.path.stem}.drift_report.md",
        f"  verdict: {decision.verdict}",
        f"  reason: {decision.reason}",
    )
