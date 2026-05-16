# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §lore_proposal_emission
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/LORE_EXTRACTION_PROTOCOL.md
"""
Handler: lore_proposal_emission

Surfaces lore-extraction candidates from closed packets.

enumerate(): scans lore_topic_dirs under docs/lore/ for entries older
  than lore_review_ttl_days that lack a companion REVIEWED or PROMOTED
  marker; separately greps docs/operations/task_*/ packets for a
  "## Lessons" section and reports them as pending-lore proposals.
  Only the packet_closure_with_lessons_section trigger is implemented;
  the remaining 4 triggers (memory_feedback_write, critic_recurring_pattern,
  missed_authority_doc_update, external_vendor_quirk) are stubbed as
  logger.debug() — detection logic not yet specified in the catalog.

apply(): always dry_run_only=True — lore emission requires human review.
  Returns mock diff showing what would be emitted to proposals_dir.

Verdict strings:
  LORE_PROPOSAL_CANDIDATE   — packet has "## Lessons" section, proposal pending
  LORE_STALE_REVIEW         — lore entry older than ttl_days without REVIEWED marker
  SKIP_ALREADY_REVIEWED     — entry has REVIEWED or PROMOTED marker
  SKIP_TOO_FRESH            — entry mtime within lore_review_ttl_days
  SKIP_NO_LESSONS_SECTION   — packet has no detectable lessons section

Deviation: triggers 2–5 (memory_feedback, critic_recurring, missed_authority,
  external_vendor_quirk) are not implemented in this batch — catalog spec
  does not define their detection logic. Logged as deviations. Each stub
  emits logger.debug() only.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import TickContext

logger = logging.getLogger(__name__)

# Verdict strings
VERDICT_PROPOSAL = "LORE_PROPOSAL_CANDIDATE"
VERDICT_STALE_REVIEW = "LORE_STALE_REVIEW"
VERDICT_SKIP_REVIEWED = "SKIP_ALREADY_REVIEWED"
VERDICT_SKIP_FRESH = "SKIP_TOO_FRESH"
VERDICT_SKIP_NO_LESSONS = "SKIP_NO_LESSONS_SECTION"

DEFAULT_TTL_DAYS = 7
DEFAULT_LORE_TOPIC_DIRS = [
    "topology", "hooks", "runtime", "data", "calibration",
    "execution", "settlement", "vendor", "browser", "identity", "packet",
]
DEFAULT_PROPOSALS_DIR = "lore_proposals"

# Heuristic: look for a ## Lessons heading (any level 2 variant)
_LESSONS_RE = re.compile(r"^#{1,3}\s+(Lessons|Lessons Learned|Lore)\b", re.MULTILINE | re.IGNORECASE)
# Reviewed/promoted marker (written by human or promote script)
_REVIEWED_RE = re.compile(r"^(REVIEWED|PROMOTED)\s*:", re.MULTILINE)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate(entry: Any, ctx: TickContext) -> list[Candidate]:  # noqa: A001
    """
    Surface lore-extraction candidates.

    Trigger 1 (implemented): scan docs/operations/task_*/ packets for
      "## Lessons" section older than lore_review_ttl_days.
    Trigger 2-5 (stubbed): not yet specified — emits logger.debug only.

    Also scans configured lore_topic_dirs under docs/lore/ for stale
    unreviewed entries.
    """
    raw: dict = entry.raw
    config: dict = raw.get("config", {})
    ttl_days: int = int(config.get("lore_review_ttl_days", DEFAULT_TTL_DAYS))
    lore_topic_dirs: list[str] = list(config.get("lore_topic_dirs", DEFAULT_LORE_TOPIC_DIRS))
    repo_root: Path = ctx.config.repo_root

    now_ts = time.time()
    ttl_seconds = ttl_days * 86400

    candidates: list[Candidate] = []

    # --- Trigger 1: closed packets with ## Lessons section ---
    candidates.extend(_scan_packet_lessons(repo_root, now_ts, ttl_seconds, ttl_days))

    # --- Trigger 2-5: stubbed ---
    _stub_triggers_2_to_5()

    # --- Stale lore entries in docs/lore/<topic>/ ---
    candidates.extend(_scan_lore_topic_dirs(repo_root, lore_topic_dirs, now_ts, ttl_seconds, ttl_days))

    return candidates


def apply(decision: Candidate, ctx: TickContext) -> ApplyResult:
    """
    Emit lore proposal. Always dry_run_only=True (live_default: false in catalog).

    TOP-OF-FUNCTION GUARD — this task is ALWAYS proposal-only.
    Returns mock diff describing what would be written to proposals_dir.
    """
    # This task is ALWAYS proposal-only — lore emission requires human review.
    mock = _mock_diff(decision)
    return ApplyResult(
        task_id="lore_proposal_emission",
        dry_run_only=True,
        diff=mock,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scan_packet_lessons(
    repo_root: Path, now_ts: float, ttl_seconds: float, ttl_days: int
) -> list[Candidate]:
    """
    Scan docs/operations/task_*/ packets for a "## Lessons" section.

    Returns candidates for packets that:
      - Have a recognizable lessons heading
      - Have mtime > ttl_days (stale, not recently updated)
      - Do not have a REVIEWED or PROMOTED marker in the lessons file
    """
    ops_dir = repo_root / "docs" / "operations"
    candidates: list[Candidate] = []

    if not ops_dir.exists():
        logger.debug("lore_proposal_emission: ops_dir missing: %s", ops_dir)
        return candidates

    for packet_dir in sorted(ops_dir.iterdir()):
        if not packet_dir.is_dir():
            continue
        if not packet_dir.name.startswith("task_"):
            continue

        # Look for lessons in any .md file within the packet
        lessons_file = _find_lessons_file(packet_dir)
        if lessons_file is None:
            candidates.append(Candidate(
                task_id="lore_proposal_emission",
                path=packet_dir,
                verdict=VERDICT_SKIP_NO_LESSONS,
                reason="no detectable ## Lessons section in packet",
                evidence={"packet": packet_dir.name},
            ))
            continue

        # Check if already reviewed
        try:
            content = lessons_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, FileNotFoundError):
            continue

        if _REVIEWED_RE.search(content):
            candidates.append(Candidate(
                task_id="lore_proposal_emission",
                path=lessons_file,
                verdict=VERDICT_SKIP_REVIEWED,
                reason="lessons section already marked REVIEWED or PROMOTED",
                evidence={"packet": packet_dir.name, "file": lessons_file.name},
            ))
            continue

        # mtime check on the packet dir (most recent activity)
        mtime = _get_mtime(packet_dir)
        if mtime is None or (now_ts - mtime) < ttl_seconds:
            age_days = round((now_ts - mtime) / 86400, 1) if mtime else None
            candidates.append(Candidate(
                task_id="lore_proposal_emission",
                path=lessons_file,
                verdict=VERDICT_SKIP_FRESH,
                reason=f"packet too recently active (age={age_days}d < {ttl_days}d)",
                evidence={"packet": packet_dir.name, "age_days": age_days},
            ))
            continue

        age_days = round((now_ts - mtime) / 86400, 1)
        candidates.append(Candidate(
            task_id="lore_proposal_emission",
            path=lessons_file,
            verdict=VERDICT_PROPOSAL,
            reason=f"packet has unreviewed Lessons section (age={age_days}d > {ttl_days}d)",
            evidence={"packet": packet_dir.name, "file": lessons_file.name, "age_days": age_days},
        ))

    return candidates


def _scan_lore_topic_dirs(
    repo_root: Path,
    lore_topic_dirs: list[str],
    now_ts: float,
    ttl_seconds: float,
    ttl_days: int,
) -> list[Candidate]:
    """
    Scan docs/lore/<topic>/ for stale unreviewed lore entry files.
    """
    lore_base = repo_root / "docs" / "lore"
    candidates: list[Candidate] = []

    if not lore_base.exists():
        logger.debug("lore_proposal_emission: lore_base missing: %s", lore_base)
        return candidates

    for topic in lore_topic_dirs:
        topic_dir = lore_base / topic
        if not topic_dir.exists():
            continue

        for entry_file in sorted(topic_dir.iterdir()):
            if not entry_file.is_file() or not entry_file.suffix == ".md":
                continue

            mtime = _get_mtime(entry_file)
            if mtime is None or (now_ts - mtime) < ttl_seconds:
                age_days = round((now_ts - mtime) / 86400, 1) if mtime else None
                candidates.append(Candidate(
                    task_id="lore_proposal_emission",
                    path=entry_file,
                    verdict=VERDICT_SKIP_FRESH,
                    reason=f"lore entry too fresh (age={age_days}d < {ttl_days}d)",
                    evidence={"topic": topic, "age_days": age_days},
                ))
                continue

            # Check for reviewed marker
            try:
                content = entry_file.read_text(encoding="utf-8", errors="replace")
            except (OSError, FileNotFoundError):
                continue

            if _REVIEWED_RE.search(content):
                candidates.append(Candidate(
                    task_id="lore_proposal_emission",
                    path=entry_file,
                    verdict=VERDICT_SKIP_REVIEWED,
                    reason="lore entry already marked REVIEWED or PROMOTED",
                    evidence={"topic": topic},
                ))
                continue

            age_days = round((now_ts - mtime) / 86400, 1)
            candidates.append(Candidate(
                task_id="lore_proposal_emission",
                path=entry_file,
                verdict=VERDICT_STALE_REVIEW,
                reason=f"stale unreviewed lore entry (age={age_days}d > {ttl_days}d)",
                evidence={"topic": topic, "age_days": age_days},
            ))

    return candidates


def _stub_triggers_2_to_5() -> None:
    """
    Triggers 2-5 are not yet implemented — catalog spec does not define
    their detection logic. Stubs emit logger.debug only.

    Deviation: logged to BATCH_C_DONE. To implement, replace each stub
    with a concrete scanner using the catalog triggers_to_scan spec.
    """
    logger.debug(
        "lore_proposal_emission: triggers 2-5 not implemented "
        "(memory_feedback_write, critic_recurring_pattern, "
        "missed_authority_doc_update, external_vendor_quirk) — "
        "detection logic not specified in catalog; deferred to WAVE 7"
    )


def _find_lessons_file(packet_dir: Path) -> Path | None:
    """
    Search packet_dir for a .md file containing a ## Lessons heading.

    Checks PLAN.md, BATCH_DONE.md, SCAFFOLD.md, and any other .md
    files at the top level of the packet directory.
    """
    priority = ["PLAN.md", "BATCH_DONE.md", "SCAFFOLD.md", "DESIGN.md"]
    candidates_to_check = []

    for name in priority:
        p = packet_dir / name
        if p.exists():
            candidates_to_check.append(p)

    for f in sorted(packet_dir.iterdir()):
        if f.is_file() and f.suffix == ".md" and f.name not in priority:
            candidates_to_check.append(f)

    for f in candidates_to_check:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            if _LESSONS_RE.search(content):
                return f
        except (OSError, FileNotFoundError):
            continue

    return None


def _get_mtime(path: Path) -> float | None:
    """Return mtime of path in seconds, or None if inaccessible."""
    try:
        return path.stat().st_mtime
    except (OSError, FileNotFoundError):
        return None


def _mock_diff(decision: Candidate) -> tuple[str, ...]:
    """Return mock diff showing planned lore proposal emission."""
    return (
        f"[DRY RUN] would emit lore proposal for: {decision.path}",
        f"       → evidence_dir/lore_proposals/{decision.path.stem}.proposal.md",
        f"  reason: {decision.reason}",
    )
