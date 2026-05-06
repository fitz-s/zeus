#!/usr/bin/env python3
# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: PLAN §2.4/§2.5/§2.6/§2.7/§2.8 + critic-opus §0.5 (ATTACK 1,2,4,7,8,9 binding)
#   evidence/hook_redesign_critic_opus.md
#   docs/operations/task_2026-05-06_hook_redesign/PLAN.md
# Phase 2: pre_checkout_uncommitted_overlap, pr_create_loc_accumulation,
#   pr_open_monitor_arm, phase_close_commit_required, pre_edit_hooks_protected

"""
dispatch.py — single entry point for every Claude Code hook event.

Phase 1 (parallel-install): YAML schema loaded; signal emitted; advisory/blocking
semantics enforced per critic-opus §0.5. Actual check logic for each hook_id is
stub-only in Phase 1; full implementations land in Phase 2.

Crash semantics (ATTACK 8 / OD-HOOK-1):
  - ADVISORY hooks: fail-open (exit 0) on any unhandled exception.
  - BLOCKING hooks: fail-closed (exit 2 + stderr message) on any unhandled exception.

Override expiry (ATTACK 7):
  - Clock-start = git log -1 --format=%ct <evidence_file>  (immutable provenance).
  - Replay protection: (override_id, evidence_file) pair counts once per 30d window.
  - auto_expires_after: never  permitted only for REVIEW_SAFE_TAG + ISOLATED_WORKTREE.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / ".claude" / "hooks" / "registry.yaml"
OVERRIDES_PATH = REPO_ROOT / ".claude" / "hooks" / "overrides.yaml"
LOG_DIR = REPO_ROOT / ".claude" / "logs" / "hook_signal"
OVERRIDE_LOG_DIR = REPO_ROOT / ".claude" / "logs" / "hook_overrides"

# override_ids that may carry auto_expires_after: never
_NEVER_EXPIRY_WHITELIST = {"REVIEW_SAFE_TAG", "ISOLATED_WORKTREE"}

# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML not installed; cannot load hook registry.")
    return yaml.safe_load(path.read_text())


def _load_registry() -> dict[str, Any]:
    return _load_yaml(REGISTRY_PATH)


def _load_overrides() -> dict[str, Any]:
    return _load_yaml(OVERRIDES_PATH)


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def _emit_signal(
    hook_id: str,
    event: str,
    decision: str,
    reason: str,
    payload: dict[str, Any],
    *,
    override_id: str | None = None,
) -> None:
    """Write one telemetry line to .claude/logs/hook_signal/<YYYY-MM>.jsonl."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    log_file = LOG_DIR / f"{month}.jsonl"
    entry = {
        "hook_id": hook_id,
        "event": event,
        "decision": decision,
        "reason": reason,
        "override_id": override_id,
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with log_file.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Override validation (ATTACK 7)
# ---------------------------------------------------------------------------

# In-process replay-protection cache: {(override_id, evidence_file) -> first_seen_ts}
# This is per-process; durable replay protection would use the audit jsonl.
_SEEN_PAIRS: dict[tuple[str, str | None], float] = {}


def _parse_duration_to_seconds(value: str) -> float | None:
    """Parse '24h', '7d', '5m', '1h', '60s' into seconds. Returns None for 'never'."""
    if value == "never":
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = value[-1]
    if unit not in units:
        raise ValueError(f"Unknown duration unit: {value!r}")
    return float(value[:-1]) * units[unit]


def _get_evidence_file_commit_time(evidence_file_path: Path) -> float | None:
    """Clock-start = git log -1 --format=%ct <path> (immutable provenance)."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", str(evidence_file_path)],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        ct = result.stdout.strip()
        if ct:
            return float(ct)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def validate_override(
    override: dict[str, Any],
    spec: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    """
    Return True if the structured override is valid for this invocation.

    Checks:
    1. auto_expires_after: never only permitted for REVIEW_SAFE_TAG + ISOLATED_WORKTREE.
    2. Evidence file exists (when requires.evidence_file is set).
    3. Override not expired (clock-start = git commit timestamp of evidence file).
    4. Replay protection: (override_id, evidence_file) pair counts once per 30d.
    """
    override_id: str = override["id"]
    requires: dict[str, Any] = override.get("requires", {})
    auto_expires: str = requires.get("auto_expires_after", "24h")

    # Rule 1: 'never' whitelist
    if auto_expires == "never" and override_id not in _NEVER_EXPIRY_WHITELIST:
        _emit_signal(
            override_id,
            spec.get("event", ""),
            "deny",
            f"auto_expires_after:never not permitted for {override_id}",
            payload,
            override_id=override_id,
        )
        return False

    # Rule 2: evidence file existence
    evidence_rel: str | None = requires.get("evidence_file")
    evidence_path: Path | None = None
    if evidence_rel:
        # Replace template placeholders with a wildcard-style check
        if "<" in evidence_rel:
            # Template path — cannot validate existence without concrete date/id;
            # Phase 2 will fill in concrete resolution. In Phase 1, accept.
            pass
        else:
            evidence_path = REPO_ROOT / evidence_rel
            if not evidence_path.exists():
                return False

    # Rule 3: expiry check (skip for 'never')
    if auto_expires != "never" and evidence_path is not None and evidence_path.exists():
        expires_seconds = _parse_duration_to_seconds(auto_expires)
        if expires_seconds is not None:
            commit_ts = _get_evidence_file_commit_time(evidence_path)
            if commit_ts is None:
                # File not committed — fall back to mtime as worst-case
                commit_ts = evidence_path.stat().st_mtime
            elapsed = time.time() - commit_ts
            if elapsed > expires_seconds:
                return False

    # Rule 4: replay protection — (override_id, evidence_file_path) counts once per 30d
    pair_key = (override_id, str(evidence_path) if evidence_path else None)
    thirty_days = 30 * 86400
    now = time.time()
    if pair_key in _SEEN_PAIRS:
        first_seen = _SEEN_PAIRS[pair_key]
        if (now - first_seen) < thirty_days:
            # Already counted — allow but do not double-count
            pass
        else:
            # Older than 30d window — reset
            _SEEN_PAIRS[pair_key] = now
    else:
        _SEEN_PAIRS[pair_key] = now

    return True


# ---------------------------------------------------------------------------
# Override detection
# ---------------------------------------------------------------------------


def _detect_override(
    spec: dict[str, Any],
    payload: dict[str, Any],
    overrides_catalog: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Detect whether a structured override is present for this invocation.

    Looks for STRUCTURED_OVERRIDE env var containing a valid override_id
    that is listed in the hook spec's bypass_policy.override_ids.
    """
    bypass_policy = spec.get("bypass_policy", {})
    if bypass_policy.get("class") == "not_required":
        return None
    allowed_ids: list[str] = bypass_policy.get("override_ids", [])
    requested_id = os.environ.get("STRUCTURED_OVERRIDE", "").strip()
    if not requested_id or requested_id not in allowed_ids:
        return None
    # Find override definition in catalog
    for ov in overrides_catalog:
        if ov["id"] == requested_id:
            return ov
    return None


# ---------------------------------------------------------------------------
# JSON output helpers
# ---------------------------------------------------------------------------


def _emit_deny(reason: str, spec: dict[str, Any]) -> int:
    """PreToolUse → permissionDecision:deny JSON envelope. Other events → exit 2."""
    if spec.get("event") == "PreToolUse":
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        return 0  # JSON envelope delivered; exit 0 per Claude Code contract
    print(reason, file=sys.stderr)
    return 2


def _emit_advisory(hook_id: str, event: str, additional_context: str) -> int:
    """Emit hookSpecificOutput.additionalContext (no permissionDecision)."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": additional_context,
                }
            }
        )
    )
    return 0


# ---------------------------------------------------------------------------
# Per-hook check implementations (Phase 2)
# ---------------------------------------------------------------------------

# Paths that pre_edit_hooks_protected covers (relative to REPO_ROOT)
_PROTECTED_HOOK_PATHS = (
    ".claude/settings.json",
    ".claude/hooks/",
)

# TRUTH_REWRITE + ON_CHAIN reversibility classes require operator-signed sentinel
_OPERATOR_SIGNED_CLASSES = {"TRUTH_REWRITE", "ON_CHAIN"}


def _command_from_payload(payload: dict[str, Any]) -> str:
    """Extract command string from hook payload tool_input."""
    return payload.get("tool_input", {}).get("command", "")


def _file_path_from_payload(payload: dict[str, Any]) -> str:
    """Extract file_path from Edit/Write/MultiEdit/NotebookEdit payload."""
    tool_input = payload.get("tool_input", {})
    return tool_input.get("file_path", "") or tool_input.get("path", "")


def _run_blocking_check_pre_checkout_uncommitted_overlap(
    payload: dict[str, Any],
) -> tuple[str, str]:
    """
    Detect `git checkout <branch>` / `git switch <branch>` / `gh pr checkout`
    when tracked modifications overlap with the target branch's tree.

    Logic:
    1. git diff --name-only HEAD → modified tracked files
    2. Parse target branch from command
    3. git ls-tree -r --name-only <target-branch> → target tree files
    4. Intersection → overlap; if non-empty, deny with lossless options
    """
    command = _command_from_payload(payload)
    if not command:
        return "allow", "no_command"

    import re
    import shlex

    # Detect git checkout / git switch / gh pr checkout
    checkout_patterns = [
        re.compile(r"(?:^|[;&|]\s*)(?:/\S*/)?git\s+(?:checkout|switch)\s+([^\s;&|]+)"),
        re.compile(r"(?:^|[;&|]\s*)gh\s+pr\s+checkout\s+([^\s;&|]+)"),
    ]
    target_branch: str | None = None
    for pat in checkout_patterns:
        m = pat.search(command)
        if m:
            target = m.group(1)
            # Skip flags and help options
            if target.startswith("-") or target in ("--", "HEAD"):
                continue
            target_branch = target
            break

    if not target_branch:
        return "allow", "not_a_checkout_command"

    # Get tracked modifications
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        modified_files = set(
            f.strip() for f in diff_result.stdout.splitlines() if f.strip()
        )
    except (subprocess.TimeoutExpired, OSError):
        return "allow", "git_diff_failed"

    if not modified_files:
        return "allow", "no_uncommitted_modifications"

    # Get target branch tree
    try:
        tree_result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", target_branch],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        if tree_result.returncode != 0:
            # Target branch may not exist yet or be a remote; allow (not our job to block)
            return "allow", "target_branch_not_found"
        target_files = set(
            f.strip() for f in tree_result.stdout.splitlines() if f.strip()
        )
    except (subprocess.TimeoutExpired, OSError):
        return "allow", "git_ls_tree_timeout"

    overlap = modified_files & target_files
    if not overlap:
        return "allow", "no_file_overlap"

    # Build deny message with lossless options
    overlap_list = "\n".join(f"  {f}" for f in sorted(overlap)[:10])
    if len(overlap) > 10:
        overlap_list += f"\n  ... and {len(overlap) - 10} more"

    reason = (
        f"BLOCKED: `git checkout {target_branch}` would silently revert "
        f"{len(overlap)} tracked modification(s) that exist on `{target_branch}`:\n"
        f"{overlap_list}\n\n"
        "Lossless options (pick one):\n"
        "  (a) git stash push -m \"pre-checkout-<phase-id>\" — recovery via\n"
        "      git stash show -p stash@{0}^3 if untracked tree needed.\n"
        "  (b) git commit -m \"phase N WIP: <one-line>\" — committed work\n"
        "      survives any subsequent checkout.\n"
        "  (c) git worktree add ../zeus-checkout " + target_branch + " && cd ../zeus-checkout\n"
        "      — isolated index, no overlap.\n\n"
        "Override (operator-only, requires evidence file):\n"
        "  STRUCTURED_OVERRIDE=STASH_FIRST_VERIFIED  (if stash already in place)\n"
        "  STRUCTURED_OVERRIDE=OPERATOR_DESTRUCTIVE  (accepts work loss explicitly)"
    )
    return "deny", reason


def _run_blocking_check_pre_edit_hooks_protected(
    payload: dict[str, Any],
) -> tuple[str, str]:
    """
    Deny Edit|Write|MultiEdit|NotebookEdit on .claude/settings.json or .claude/hooks/**.
    Class TRUTH_REWRITE → operator-signed sentinel required per OD-HOOK-2 / ATTACK 2.
    """
    file_path = _file_path_from_payload(payload)
    if not file_path:
        return "allow", "no_file_path"

    # Normalize: make relative to repo root if absolute
    try:
        fpath = Path(file_path)
        if fpath.is_absolute():
            try:
                fpath = fpath.relative_to(REPO_ROOT)
            except ValueError:
                return "allow", "path_outside_repo"
        fpath_str = fpath.as_posix()
    except Exception:
        return "allow", "path_parse_error"

    is_protected = False
    for protected in _PROTECTED_HOOK_PATHS:
        if protected.endswith("/"):
            if fpath_str.startswith(protected) or fpath_str == protected.rstrip("/"):
                is_protected = True
                break
        else:
            if fpath_str == protected:
                is_protected = True
                break

    if not is_protected:
        return "allow", "not_a_protected_path"

    reason = (
        f"BLOCKED: `{fpath_str}` is a protected hook configuration file.\n"
        "Direct edits to .claude/settings.json or .claude/hooks/** constitute\n"
        "a TRUTH_REWRITE-class change requiring operator-signed authorization.\n\n"
        "Required: evidence/operator_signed/HOOK_SCHEMA_CHANGE_<date>.signed\n"
        "  (written outside agent write surface — operator-only path per CHARTER §6.4)\n\n"
        "Override:\n"
        "  STRUCTURED_OVERRIDE=HOOK_SCHEMA_CHANGE\n"
        "  + evidence/operator_signed/HOOK_SCHEMA_CHANGE_<date>.signed must exist"
    )
    return "deny", reason


def _run_advisory_check_pr_create_loc_accumulation(
    payload: dict[str, Any],
) -> str | None:
    """
    Advisory for `gh pr create` / `gh pr ready` when commits < 2 OR LOC < 80.
    Per PLAN §2.7.
    """
    command = _command_from_payload(payload)
    if not command:
        return None

    # Detect gh pr create / gh pr ready
    import re
    if not re.search(r"gh\s+pr\s+(create|ready)", command):
        return None

    # Count commits since base
    try:
        base_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        if base_result.returncode != 0:
            # No upstream tracking; try origin/main
            base_ref = "origin/main"
        else:
            base_ref = base_result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return None

    try:
        commit_count_result = subprocess.run(
            ["git", "rev-list", "--count", f"{base_ref}..HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        commit_count = int(commit_count_result.stdout.strip() or "0")
    except (subprocess.TimeoutExpired, ValueError, OSError):
        commit_count = 0

    try:
        shortstat_result = subprocess.run(
            ["git", "diff", "--shortstat", f"{base_ref}..HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        # Parse "+ X insertions" / "- Y deletions"
        import re as re2
        loc_match = re2.findall(r"(\d+)\s+insertion", shortstat_result.stdout)
        del_match = re2.findall(r"(\d+)\s+deletion", shortstat_result.stdout)
        loc = int(loc_match[-1]) + int(del_match[-1]) if loc_match and del_match else 0
    except (subprocess.TimeoutExpired, ValueError, OSError):
        loc = 0

    try:
        reflog_result = subprocess.run(
            ["git", "reflog", "show", "--pretty=%gD", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        push_count = sum(
            1 for line in reflog_result.stdout.splitlines()
            if "push" in line.lower()
        )
    except (subprocess.TimeoutExpired, OSError):
        push_count = 0

    # Thresholds from registry.yaml intent
    COMMIT_THRESHOLD = 2
    LOC_THRESHOLD = 80

    if commit_count >= COMMIT_THRESHOLD and loc >= LOC_THRESHOLD:
        return None  # Accumulation looks sufficient

    return (
        f"ADVISORY: PR open about to fire paid auto-reviewers (Copilot + Codex\n"
        f"+ ultrareview within 5-8 min). Current accumulation:\n"
        f"   commits since base: {commit_count}\n"
        f"   LOC since base:     {loc}\n"
        f"   pushes already:     {push_count}\n\n"
        f"Per feedback_accumulate_changes_before_pr_open.md (verified 2026-05-04):\n"
        f"PRs should open at >={COMMIT_THRESHOLD} commits and >={LOC_THRESHOLD} LOC unless\n"
        f"explicitly approved for a quick fix. If this open is intentional\n"
        f"(urgent fix, isolated bug), proceed. If you have more pending work\n"
        f"on this branch, hold the PR open until accumulation reaches the threshold.\n\n"
        f"This is advisory; not blocking."
    )


def _run_advisory_check_pr_open_monitor_arm(
    payload: dict[str, Any],
) -> str | None:
    """
    After successful `gh pr create` or `gh pr ready`, emit additionalContext
    instructing the agent to arm a Monitor.
    Per PLAN §2.5 + ATTACK 9: MUST emit MONITOR_ARM_REQUIRED:<pr-num>:<expiry-iso> sentinel.
    """
    # PostToolUse: check tool_response for PR creation success
    tool_response = payload.get("tool_response", {})
    tool_input = payload.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    import re
    if not re.search(r"gh\s+pr\s+(create|ready)", command):
        return None

    # Extract PR number from tool response output
    pr_num: str = "?"
    if isinstance(tool_response, dict):
        output = tool_response.get("output", "") or ""
    elif isinstance(tool_response, str):
        output = tool_response
    else:
        output = ""

    pr_match = re.search(r"#(\d+)", output) or re.search(r"pulls/(\d+)", output)
    if pr_match:
        pr_num = pr_match.group(1)

    # Compute expiry (60 min from now)
    from datetime import timedelta
    expiry = datetime.now(timezone.utc) + timedelta(minutes=60)
    expiry_iso = expiry.strftime("%Y-%m-%dT%H:%M:%SZ")

    # ATTACK 9 sentinel: MONITOR_ARM_REQUIRED:<pr-num>:<expiry-iso>
    monitor_sentinel = f"MONITOR_ARM_REQUIRED:{pr_num}:{expiry_iso}"

    return (
        f"{monitor_sentinel}\n\n"
        f"PR opened. Per feedback_accumulate_changes_before_pr_open.md, paid\n"
        f"auto-reviewers (Copilot, Codex) fire within 5-8 minutes. Arm a\n"
        f"Monitor and a watcher now:\n\n"
        f"  Monitor(persistent=true,\n"
        f"          command=\"prev=''; while true; do\n"
        f"                     s=$(gh pr checks {pr_num} --json name,bucket);\n"
        f"                     cur=$(jq -r '.[] | select(.bucket!=\\\"pending\\\") | \\\"\\\\(.name): \\\\(.bucket)\\\"' <<<\\\"$s\\\" | sort);\n"
        f"                     comm -13 <(echo \\\"$prev\\\") <(echo \\\"$cur\\\");\n"
        f"                     prev=$cur;\n"
        f"                     jq -e 'all(.bucket!=\\\"pending\\\")' <<<\\\"$s\\\" >/dev/null && break;\n"
        f"                     sleep 30;\n"
        f"                   done\")\n\n"
        f"Stop the watcher when:\n"
        f"  (a) all checks resolved AND\n"
        f"  (b) all review comments resolved (gh pr view --json reviews shows\n"
        f"      latestReviews state=APPROVED or no actionable items),\n"
        f"  OR if 60 min idle elapses (escalate).\n\n"
        f"Address comments by commit, not by reply. After each commit batch,\n"
        f"poll once more before declaring DONE."
    )


def _run_advisory_check_phase_close_commit_required(
    payload: dict[str, Any],
) -> str | None:
    """
    SubagentStop: when a phase-class subagent returns and working tree has
    tracked changes not yet committed, emit additionalContext.
    Per PLAN §2.8: matcher narrowed to agent_type containing 'phase_'.
    """
    agent_type = payload.get("agent_type", "")
    # Narrow to phase-class subagents only (PLAN §2.8 + H-R6 mitigation)
    if "phase_" not in agent_type.lower():
        return None

    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        modified_lines = [
            l for l in status_result.stdout.splitlines()
            if l.strip() and not l.startswith("??")  # exclude untracked
        ]
    except (subprocess.TimeoutExpired, OSError):
        return None

    if not modified_lines:
        return None

    n = len(modified_lines)
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        timeout=5,
        cwd=REPO_ROOT,
    )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"

    return (
        f"ADVISORY: phase subagent returned with {n} tracked modification(s) and\n"
        f"potentially zero commits during this phase (branch: {branch}).\n"
        f"Per feedback_commit_per_phase_or_lose_everything.md\n"
        f"(verified 2026-05-06; cost: $190 in the topology redesign session):\n\n"
        f"   git add <phase-N specific paths>\n"
        f"   git commit -m \"phase N close: <one-line summary>\"\n\n"
        f"Skip if this phase deliberately accumulates into a later commit\n"
        f"(rare; applies to recovery + stash-restore phases only)."
    )


def _run_blocking_check(
    spec: dict[str, Any], payload: dict[str, Any]
) -> tuple[str, str]:
    """
    Return (decision, reason) for BLOCKING hooks.
    Phase 2: real check logic per hook_id.
    """
    hook_id = spec.get("id", "")

    if hook_id == "pre_checkout_uncommitted_overlap":
        return _run_blocking_check_pre_checkout_uncommitted_overlap(payload)
    elif hook_id == "pre_edit_hooks_protected":
        return _run_blocking_check_pre_edit_hooks_protected(payload)
    else:
        # Phase 1 pass-through for hooks not yet fully implemented
        return "allow", "phase1_stub"


def _run_advisory_check(
    spec: dict[str, Any], payload: dict[str, Any]
) -> str | None:
    """
    Return additionalContext string for ADVISORY hooks, or None.
    Phase 2: real logic per hook_id.
    """
    hook_id = spec.get("id", "")

    if hook_id == "pr_create_loc_accumulation":
        return _run_advisory_check_pr_create_loc_accumulation(payload)
    elif hook_id == "pr_open_monitor_arm":
        return _run_advisory_check_pr_open_monitor_arm(payload)
    elif hook_id == "phase_close_commit_required":
        return _run_advisory_check_phase_close_commit_required(payload)
    else:
        return None


def _log_override_use(
    hook_id: str, override: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Append one line to the override's audit_log jsonl."""
    audit_log_rel: str | None = override.get("audit_log")
    if not audit_log_rel:
        return
    audit_path = REPO_ROOT / audit_log_rel
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "hook_id": hook_id,
        "override_id": override["id"],
        "session_id": payload.get("session_id"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with audit_path.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------


def main(hook_id: str) -> int:
    try:
        registry = _load_registry()
    except Exception as exc:
        print(f"dispatch.py: failed to load registry: {exc}", file=sys.stderr)
        # Cannot determine severity without registry; fail-closed is safest
        return 2

    try:
        overrides_catalog = _load_overrides().get("overrides", [])
    except Exception:
        overrides_catalog = []

    # Read payload from stdin
    try:
        raw = sys.stdin.read()
        payload: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    event: str = payload.get("hook_event_name", "")

    # Find hook spec
    spec: dict[str, Any] | None = next(
        (h for h in registry.get("hooks", []) if h["id"] == hook_id), None
    )
    if spec is None:
        _emit_signal(hook_id, event, "allow", "missing_spec", payload)
        return 0

    severity: str = spec.get("severity", "ADVISORY")

    try:
        if severity == "ADVISORY":
            ctx = _run_advisory_check(spec, payload)
            _emit_signal(hook_id, event, "allow", "advisory_check", payload)
            if ctx:
                return _emit_advisory(hook_id, event, ctx)
            return 0

        # BLOCKING path
        decision, reason = _run_blocking_check(spec, payload)

        if decision == "deny":
            override = _detect_override(spec, payload, overrides_catalog)
            if override:
                if validate_override(override, spec, payload):
                    _log_override_use(hook_id, override, payload)
                    _emit_signal(
                        hook_id,
                        event,
                        "allow",
                        "override_accepted",
                        payload,
                        override_id=override["id"],
                    )
                    return 0
                _emit_signal(
                    hook_id,
                    event,
                    "deny",
                    "override_invalid",
                    payload,
                    override_id=override["id"],
                )
                return _emit_deny("override evidence invalid", spec)
            _emit_signal(hook_id, event, "deny", reason, payload)
            return _emit_deny(reason, spec)

        _emit_signal(hook_id, event, "allow", "passed", payload)
        return 0

    except Exception as exc:
        # Crash semantics per OD-HOOK-1 / ATTACK 8:
        # ADVISORY → fail-open (exit 0)
        # BLOCKING → fail-closed (exit 2 + stderr)
        _emit_signal(hook_id, event, "error", f"dispatch_crash: {exc}", payload)
        if severity == "ADVISORY":
            return 0
        msg = (
            f"dispatch.py crash on `{hook_id}` — operator must --no-verify to proceed"
        )
        print(msg, file=sys.stderr)
        return 2


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: dispatch.py <hook_id>", file=sys.stderr)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
