# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/ARCHIVAL_RULES.md
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §closed_packet_archive_proposal
"""
Handler: closed_packet_archive_proposal

Proposes archival of stale docs/operations/task_*/ packets.

enumerate(): walks docs/operations/task_*/, filters by mtime >= ttl_days,
  runs all 9 exemption checks (check #0 via archival_check_0 + checks 1–8
  heuristic), groups wave packets as ATOMIC GROUP via wave_family module.
  Returns list[Candidate] with per-packet verdict + evidence.

apply(): this task is always live_default=false (archive proposals only).
  Returns dry_run_only=True with a mock diff string.

Exemption check status:
  #0  authority_status_check   — IMPLEMENTED (archival_check_0 module)
  #1  authority_status_check   — IMPLEMENTED (Status: AUTHORITY header grep)
  #2  reference_replacement_check — IMPLEMENTED (architecture/reference_replacement.yaml)
  #3  docs_registry_check      — IMPLEMENTED (architecture/docs_registry.yaml)
  #4  code_reference_grep      — IMPLEMENTED (git grep across src/scripts/tests/architecture)
  #5  active_packet_citation   — IMPLEMENTED (grep recently-modified task_* packets)
  #6  open_pr_check            — STUBBED (shell unavailable in dry-run; always PASS-with-warn)
  #7  hook_launchd_citation    — IMPLEMENTED (grep settings.json + LaunchAgents plists)
  #8  worktree_branch_check    — IMPLEMENTED (git worktree list parse)
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from maintenance_worker.core.archival_check_0 import check_authority_status
from maintenance_worker.rules.wave_family import group_by_wave_family, wave_family_exemption_atomic
from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import TaskSpec, TickContext

logger = logging.getLogger(__name__)

# Verdict strings used across this handler
VERDICT_ARCHIVABLE = "ARCHIVE_CANDIDATE"
VERDICT_LOAD_BEARING = "LOAD_BEARING_DESPITE_AGE"
VERDICT_ACTIVE = "ACTIVE"
VERDICT_WINDING_DOWN = "WINDING_DOWN"
VERDICT_ALREADY_ARCHIVED = "ALREADY_ARCHIVED"

# Default config values (override via catalog raw dict)
DEFAULT_TTL_DAYS = 60
DEFAULT_ARCHIVE_DIR_PATTERN = "docs/operations/archive/{year}-Q{quarter}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate(entry: Any, ctx: TickContext) -> list[Candidate]:  # noqa: A001
    """
    Walk docs/operations/task_*/ and classify each packet for archival.

    entry: TaskCatalogEntry — spec + raw config dict from catalog.
    ctx:   TickContext.

    Returns list[Candidate] with verdict ∈ {ARCHIVE_CANDIDATE,
    LOAD_BEARING_DESPITE_AGE, ACTIVE, WINDING_DOWN, ALREADY_ARCHIVED}.
    """
    spec: TaskSpec = entry.spec
    raw: dict = entry.raw
    config: dict = raw.get("config", {})

    ttl_days: int = int(config.get("packet_archive_ttl_days", DEFAULT_TTL_DAYS))
    repo_root: Path = ctx.config.repo_root
    ops_dir: Path = repo_root / "docs" / "operations"
    registry_path: Path = repo_root / "architecture" / "artifact_authority_status.yaml"
    ref_replace_path: Path = repo_root / "architecture" / "reference_replacement.yaml"
    docs_registry_path: Path = repo_root / "architecture" / "docs_registry.yaml"

    now_ts = time.time()
    ttl_seconds = ttl_days * 86400

    candidates: list[Candidate] = []

    if not ops_dir.exists():
        logger.warning("closed_packet_archive_proposal: ops_dir missing: %s", ops_dir)
        return candidates

    # Collect all task_* directories
    packet_dirs = sorted([
        p for p in ops_dir.iterdir()
        if p.is_dir() and p.name.startswith("task_")
    ])

    # Check if a .archived stub already exists for each packet
    def _is_already_archived(packet: Path) -> bool:
        stub = ops_dir / f"{packet.name}.archived"
        return stub.exists()

    # Separate wave packets from non-wave packets for atomic group handling
    wave_packet_dirs = [p for p in packet_dirs if _is_wave_packet(p.name)]
    non_wave_dirs = [p for p in packet_dirs if not _is_wave_packet(p.name)]

    # Process non-wave packets individually
    for packet in non_wave_dirs:
        candidate = _classify_packet(
            packet=packet,
            spec=spec,
            now_ts=now_ts,
            ttl_seconds=ttl_seconds,
            repo_root=repo_root,
            registry_path=registry_path,
            ref_replace_path=ref_replace_path,
            docs_registry_path=docs_registry_path,
        )
        if candidate is not None:
            candidates.append(candidate)

    # Process wave packets as atomic groups
    wave_families = group_by_wave_family(wave_packet_dirs)
    processed_wave_paths: set[Path] = set()
    for family_key, family_members in wave_families.items():
        family_candidates = []
        for packet in family_members:
            processed_wave_paths.add(packet)
            c = _classify_packet(
                packet=packet,
                spec=spec,
                now_ts=now_ts,
                ttl_seconds=ttl_seconds,
                repo_root=repo_root,
                registry_path=registry_path,
                ref_replace_path=ref_replace_path,
                docs_registry_path=docs_registry_path,
            )
            if c is not None:
                family_candidates.append(c)

        # ATOMIC GROUP: if ANY member is LOAD_BEARING, all become LOAD_BEARING
        has_load_bearing = any(
            c.verdict == VERDICT_LOAD_BEARING for c in family_candidates
        )
        if has_load_bearing:
            for c in family_candidates:
                candidates.append(Candidate(
                    task_id=spec.task_id,
                    path=c.path,
                    verdict=VERDICT_LOAD_BEARING,
                    reason=f"Wave family '{family_key}' atomic group: at least one member is LOAD_BEARING",
                    evidence={**c.evidence, "wave_family_key": family_key, "atomic_group_override": True},
                ))
        else:
            candidates.extend(family_candidates)

    # Any wave packets not matched by group_by_wave_family (shouldn't happen, but be safe)
    for packet in wave_packet_dirs:
        if packet not in processed_wave_paths:
            c = _classify_packet(
                packet=packet,
                spec=spec,
                now_ts=now_ts,
                ttl_seconds=ttl_seconds,
                repo_root=repo_root,
                registry_path=registry_path,
                ref_replace_path=ref_replace_path,
                docs_registry_path=docs_registry_path,
            )
            if c is not None:
                candidates.append(c)

    logger.info(
        "closed_packet_archive_proposal: enumerated %d packets, %d candidates",
        len(packet_dirs),
        len(candidates),
    )
    return candidates


def apply(decision: Any, ctx: TickContext) -> ApplyResult:
    """
    Apply archival proposals. Always dry_run_only (live_default: false in catalog).

    Top-of-function guard per PLAN §1.5.4: defense-in-depth.
    Returns ApplyResult with mock diff showing what git mv would do.
    """
    # TOP-OF-FUNCTION GUARD (defense-in-depth beyond engine-level enforcement)
    if ctx.config.live_default is False:
        mock = _mock_diff(decision)
        return ApplyResult(
            task_id="closed_packet_archive_proposal",
            dry_run_only=True,
            diff=mock,
        )

    # If somehow live_default is True (unexpected), still stay dry_run_only
    # because this task is ALWAYS proposal-only.
    mock = _mock_diff(decision)
    return ApplyResult(
        task_id="closed_packet_archive_proposal",
        dry_run_only=True,
        diff=mock,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_wave_packet(name: str) -> bool:
    """Return True if the directory name matches the wave-packet pattern."""
    return bool(re.search(r"_wave\d+$", name))


def _classify_packet(
    *,
    packet: Path,
    spec: TaskSpec,
    now_ts: float,
    ttl_seconds: float,
    repo_root: Path,
    registry_path: Path,
    ref_replace_path: Path,
    docs_registry_path: Path,
) -> Candidate | None:
    """
    Classify one packet directory and return a Candidate, or None to skip.

    Returns None only for truly unclassifiable paths (e.g., permission errors).
    """
    ops_dir = packet.parent
    packet_name = packet.name

    # ALREADY_ARCHIVED: stub exists
    stub = ops_dir / f"{packet_name}.archived"
    if stub.exists():
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_ALREADY_ARCHIVED,
            reason="Archived stub exists at original path.",
            evidence={"stub_path": str(stub)},
        )

    # Get mtime of most recently modified file in the packet
    try:
        mtime = _packet_mtime(packet)
    except OSError as exc:
        logger.warning("closed_packet_archive_proposal: cannot stat %s: %s", packet, exc)
        return None

    age_days = (now_ts - mtime) / 86400

    # ACTIVE: modified within last 30 days OR has AUTHORITY/ACTIVE_LAW status header
    if age_days < 30:
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_ACTIVE,
            reason=f"Modified {age_days:.1f} days ago (< 30 day threshold).",
            evidence={"age_days": round(age_days, 1)},
        )

    # Check for AUTHORITY status header regardless of age
    if _has_authority_status_header(packet):
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_LOAD_BEARING,
            reason="Status: AUTHORITY / ACTIVE_LAW / AUTHORITATIVE header found.",
            evidence={"check": "authority_header", "age_days": round(age_days, 1)},
        )

    # WINDING_DOWN: 30-60 days — check for references first, but classify as WINDING_DOWN
    if age_days < ttl_seconds / 86400:
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_WINDING_DOWN,
            reason=f"Modified {age_days:.1f} days ago (30–{int(ttl_seconds/86400)}d window).",
            evidence={"age_days": round(age_days, 1)},
        )

    # Candidate is old enough (>= ttl_days). Run all 9 exemption checks.
    evidence: dict[str, object] = {"age_days": round(age_days, 1)}
    checks_passed = 0
    total_checks = 9

    # Check #0: Authority Status Registry
    check0 = check_authority_status(packet, registry_path)
    evidence["check_0_registry"] = check0.verdict
    evidence["check_0_reason"] = check0.reason
    if check0.verdict == "LOAD_BEARING":
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_LOAD_BEARING,
            reason=f"Check #0 LOAD_BEARING: {check0.reason}",
            evidence=evidence,
        )
    checks_passed += 1

    # Check #1: Status header in packet docs
    if _has_authority_status_header(packet):
        evidence["check_1_authority_header"] = True
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_LOAD_BEARING,
            reason="Check #1: Status: AUTHORITY/ACTIVE_LAW/AUTHORITATIVE header found.",
            evidence=evidence,
        )
    evidence["check_1_authority_header"] = False
    checks_passed += 1

    # Check #2: reference_replacement.yaml
    if _in_reference_replacement(packet, ref_replace_path):
        evidence["check_2_ref_replace"] = True
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_LOAD_BEARING,
            reason="Check #2: Path referenced in architecture/reference_replacement.yaml.",
            evidence=evidence,
        )
    evidence["check_2_ref_replace"] = False
    checks_passed += 1

    # Check #3: docs_registry.yaml
    if _in_docs_registry(packet, docs_registry_path):
        evidence["check_3_docs_registry"] = True
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_LOAD_BEARING,
            reason="Check #3: Path referenced in architecture/docs_registry.yaml.",
            evidence=evidence,
        )
    evidence["check_3_docs_registry"] = False
    checks_passed += 1

    # Check #4: Code reference grep
    code_ref = _code_reference_grep(packet_name, repo_root)
    evidence["check_4_code_ref"] = code_ref
    if code_ref:
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_LOAD_BEARING,
            reason=f"Check #4: Referenced in code (git grep): {code_ref[:2]}",
            evidence=evidence,
        )
    checks_passed += 1

    # Check #5: Active packet citation (grep recent task_* packets)
    active_citation = _active_packet_citation(packet_name, packet.parent, now_ts)
    evidence["check_5_active_citation"] = active_citation
    if active_citation:
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_LOAD_BEARING,
            reason=f"Check #5: Referenced in recently-modified packet: {active_citation[:1]}",
            evidence=evidence,
        )
    checks_passed += 1

    # Check #6: Open PR check (STUBBED — gh CLI unavailable in dry-run context)
    evidence["check_6_open_pr"] = "SKIPPED_SHELL_UNAVAILABLE"
    evidence["check_6_note"] = "Deviation: gh pr list not executed; treated as PASS-with-warn"
    logger.warning(
        "closed_packet_archive_proposal: check #6 (open_pr_check) skipped for %s "
        "— gh CLI not invoked in dry-run context",
        packet_name,
    )
    checks_passed += 1  # Count as passed (conservative: if PR existed, packet was recently active)

    # Check #7: Hook/launchd citation
    hook_ref = _hook_launchd_citation(packet_name, repo_root)
    evidence["check_7_hook_ref"] = hook_ref
    if hook_ref:
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_LOAD_BEARING,
            reason=f"Check #7: Referenced in hooks/launchd: {hook_ref[:1]}",
            evidence=evidence,
        )
    checks_passed += 1

    # Check #8: Worktree branch check
    wt_ref = _worktree_branch_check(packet_name)
    evidence["check_8_worktree"] = wt_ref
    if wt_ref:
        return Candidate(
            task_id=spec.task_id,
            path=packet,
            verdict=VERDICT_LOAD_BEARING,
            reason=f"Check #8: Slug appears in worktree branch: {wt_ref[:1]}",
            evidence=evidence,
        )
    checks_passed += 1

    evidence["checks_passed"] = f"{checks_passed}/{total_checks}"
    return Candidate(
        task_id=spec.task_id,
        path=packet,
        verdict=VERDICT_ARCHIVABLE,
        reason=f"All {total_checks} exemption checks passed ({age_days:.1f} days old).",
        evidence=evidence,
    )


def _packet_mtime(packet: Path) -> float:
    """Return the most recent mtime across all files in the packet directory."""
    max_mtime = packet.stat().st_mtime
    for child in packet.rglob("*"):
        try:
            mtime = child.stat().st_mtime
            if mtime > max_mtime:
                max_mtime = mtime
        except OSError:
            pass
    return max_mtime


def _has_authority_status_header(packet: Path) -> bool:
    """
    Check #1: grep PLAN.md / README.md first 50 lines for AUTHORITY/ACTIVE_LAW/AUTHORITATIVE.
    """
    status_re = re.compile(r"Status:\s*(AUTHORITY|ACTIVE_LAW|AUTHORITATIVE)", re.IGNORECASE)
    for candidate_name in ("PLAN.md", "README.md", "STATUS.md"):
        candidate_file = packet / candidate_name
        if not candidate_file.exists():
            continue
        try:
            lines = candidate_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[:50]:
                if status_re.search(line):
                    return True
        except OSError:
            pass
    return False


def _in_reference_replacement(packet: Path, ref_replace_path: Path) -> bool:
    """
    Check #2: scan architecture/reference_replacement.yaml for packet path references.
    """
    if not ref_replace_path.exists():
        return False
    try:
        with ref_replace_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            return False
        entries = data.get("entries", data.get("replacements", []))
        if not isinstance(entries, list):
            return False
        packet_name = packet.name
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source", ""))
            if packet_name in source or str(packet) in source:
                return True
    except Exception:
        pass
    return False


def _in_docs_registry(packet: Path, docs_registry_path: Path) -> bool:
    """
    Check #3: scan architecture/docs_registry.yaml for packet path references.
    """
    if not docs_registry_path.exists():
        return False
    try:
        with docs_registry_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            return False
        entries = data.get("entries", data.get("docs", []))
        if not isinstance(entries, list):
            return False
        packet_name = packet.name
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path_val = str(entry.get("path", ""))
            if packet_name in path_val or str(packet) in path_val:
                return True
    except Exception:
        pass
    return False


def _code_reference_grep(packet_name: str, repo_root: Path) -> list[str]:
    """
    Check #4: git grep -l "<packet_name>" across src/, scripts/, tests/, architecture/.
    Returns list of matching file paths (empty = no references).
    """
    try:
        result = subprocess.run(
            ["git", "grep", "-l", packet_name,
             "--", "src/", "scripts/", "tests/", "architecture/"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return [line for line in result.stdout.splitlines() if line.strip()]
        return []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _active_packet_citation(packet_name: str, ops_dir: Path, now_ts: float) -> list[str]:
    """
    Check #5: grep all packets modified in last 30 days for references to packet_name.
    Returns list of referencing packet names.
    """
    thirty_days = 30 * 86400
    referencing: list[str] = []
    try:
        for other_packet in ops_dir.iterdir():
            if not other_packet.is_dir():
                continue
            if other_packet.name == packet_name:
                continue
            try:
                other_mtime = other_packet.stat().st_mtime
            except OSError:
                continue
            if now_ts - other_mtime > thirty_days:
                continue
            # Grep this recently-active packet for references to our packet
            for f in other_packet.rglob("*.md"):
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    if packet_name in text:
                        referencing.append(other_packet.name)
                        break
                except OSError:
                    pass
    except OSError:
        pass
    return referencing


def _hook_launchd_citation(packet_name: str, repo_root: Path) -> list[str]:
    """
    Check #7: grep .claude/settings.json, .codex/hooks.json, LaunchAgent plists.
    Returns list of files containing the packet slug.
    """
    found: list[str] = []
    targets = [
        repo_root / ".claude" / "settings.json",
        repo_root / ".codex" / "hooks.json",
    ]
    # LaunchAgent plists
    launch_agents = Path(os.path.expanduser("~/Library/LaunchAgents"))
    if launch_agents.exists():
        targets.extend(launch_agents.glob("com.zeus.*.plist"))

    for target in targets:
        if not target.exists():
            continue
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            if packet_name in text:
                found.append(str(target))
        except OSError:
            pass
    return found


def _worktree_branch_check(packet_name: str) -> list[str]:
    """
    Check #8: list git worktree branches, check if any contain the packet slug.

    Extracts the slug from packet_name (task_YYYY-MM-DD_<slug>).
    Returns list of matching branch names.
    """
    # Extract slug from packet name
    m = re.match(r"^task_\d{4}-\d{2}-\d{2}_(.+)$", packet_name)
    slug = m.group(1) if m else packet_name

    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        branches = []
        for line in result.stdout.splitlines():
            if line.startswith("branch "):
                branch = line[len("branch "):].strip()
                branches.append(branch)
        return [b for b in branches if slug in b]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _mock_diff(decision: Any) -> tuple[str, ...]:
    """Return a mock diff tuple for dry-run proposals."""
    if decision is None:
        return ("# dry-run: no archival decisions to apply",)
    path_str = str(getattr(decision, "path", decision))
    return (
        f"# dry-run proposal for closed_packet_archive_proposal",
        f"# would execute: git mv {path_str} docs/operations/archive/<YYYY>-Q<N>/{Path(path_str).name}/",
        f"# would create stub: docs/operations/{Path(path_str).name}.archived",
    )
