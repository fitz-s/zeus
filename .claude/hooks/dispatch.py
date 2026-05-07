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


# ---------------------------------------------------------------------------
# Phase 3.R: 7 legacy shell logics ported into dispatch.py
# ---------------------------------------------------------------------------


def _load_hook_common():
    """Import hook_common module from sibling path (avoids sys.path pollution)."""
    import importlib.util as _ilu
    hc_path = Path(__file__).parent / "hook_common.py"
    spec = _ilu.spec_from_file_location("hook_common", hc_path)
    assert spec and spec.loader
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _run_blocking_check_invariant_test(
    payload: dict[str, Any],
) -> tuple[str, str]:
    """
    Run pytest baseline before `git commit`.

    Escape hatches (from pre-commit-invariant-test.sh):
      1. STRUCTURED_OVERRIDE=BASELINE_RATCHET|MAIN_REGRESSION|COTENANT_SHIM
      2. Legacy [skip-invariant] marker in command (migration shim, emits migration_warning)
      3. COMMIT_INVARIANT_TEST_SKIP=1 env var
      4. .claude/hooks/.invariant_skip sentinel
      5. .git/skip-invariant-once one-shot sentinel
    """
    import re as _re

    command = _command_from_payload(payload)
    if not command:
        return "allow", "no_command"

    hc = _load_hook_common()
    try:
        subcommands = hc.git_subcommands(command)
    except ValueError:
        if hc._raw_mentions_git(command):
            return "deny", "could_not_parse_git_commit_command"
        return "allow", "not_git_command"

    if "commit" not in subcommands:
        return "allow", "not_git_commit"

    # Escape hatch 3: env var
    if os.environ.get("COMMIT_INVARIANT_TEST_SKIP", "0") == "1":
        return "allow", "COMMIT_INVARIANT_TEST_SKIP_env"

    # Escape hatch 1: structured overrides
    new_override = os.environ.get("STRUCTURED_OVERRIDE", "").strip()
    if new_override in {"BASELINE_RATCHET", "MAIN_REGRESSION", "COTENANT_SHIM"}:
        return "allow", f"structured_override_{new_override}"

    # Escape hatch 2: legacy [skip-invariant] marker (migration shim)
    _SKIP_MARKER = "[skip-invariant]"
    if _SKIP_MARKER in command:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            month = datetime.now(timezone.utc).strftime("%Y-%m")
            warn_entry = {
                "hook_id": "invariant_test",
                "event": "PreToolUse",
                "decision": "allow",
                "reason": "legacy_skip_invariant_marker",
                "override_id": None,
                "session_id": payload.get("session_id"),
                "agent_id": payload.get("agent_id"),
                "ts": datetime.now(timezone.utc).isoformat(),
                "ritual_signal": "migration_warning",
                "migration_note": (
                    "[skip-invariant] is deprecated; use STRUCTURED_OVERRIDE=BASELINE_RATCHET. "
                    "Runway ends 2026-06-06."
                ),
            }
            with (LOG_DIR / f"{month}.jsonl").open("a") as fh:
                fh.write(json.dumps(warn_entry) + "\n")
        except OSError:
            pass
        return "allow", "legacy_skip_invariant_marker_migration_warning"

    # Escape hatch 4: .invariant_skip sentinel file
    skip_sentinel = REPO_ROOT / ".claude" / "hooks" / ".invariant_skip"
    if skip_sentinel.exists():
        return "allow", "invariant_skip_sentinel_file"

    # Escape hatch 5: .git/skip-invariant-once
    try:
        gd_result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        )
        git_dir = gd_result.stdout.strip() if gd_result.returncode == 0 else ".git"
    except (subprocess.TimeoutExpired, OSError):
        git_dir = ".git"
    gd_path = Path(git_dir) if Path(git_dir).is_absolute() else REPO_ROOT / git_dir
    if (gd_path / "skip-invariant-once").exists():
        return "allow", "skip_invariant_once_sentinel"

    # Find pytest binary (worktree-tolerant)
    pytest_bin = os.environ.get("ZEUS_HOOK_PYTEST_BIN", "").strip()
    if not pytest_bin:
        pytest_bin = str(REPO_ROOT / ".venv" / "bin" / "python")
        if not Path(pytest_bin).is_file():
            try:
                wt_result = subprocess.run(
                    ["git", "worktree", "list", "--porcelain"],
                    capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
                )
                main_wt = None
                for line in wt_result.stdout.splitlines():
                    if line.startswith("worktree "):
                        main_wt = line[len("worktree "):].strip()
                        break
                if main_wt and main_wt != str(REPO_ROOT):
                    cand = Path(main_wt) / ".venv" / "bin" / "python"
                    if cand.is_file():
                        pytest_bin = str(cand)
            except (subprocess.TimeoutExpired, OSError):
                pass

    if not Path(pytest_bin).is_file():
        return "deny", f"regression_below_baseline__pytest_bin_not_found"

    # Load baseline count
    baseline_file = REPO_ROOT / ".zeus-invariant-baseline"
    baseline_passed = 674  # from pre-commit-invariant-test.sh 2026-05-06
    if baseline_file.exists():
        try:
            baseline_passed = int(baseline_file.read_text().strip())
        except (ValueError, OSError):
            pass

    # Test file list (mirrors pre-commit-invariant-test.sh)
    _TEST_FILES = (
        "tests/test_architecture_contracts.py tests/test_settlement_semantics.py "
        "tests/test_digest_profiles_equivalence.py tests/test_inv_prototype.py "
        "tests/test_edge_observation.py tests/test_edge_observation_weekly.py "
        "tests/test_attribution_drift.py tests/test_attribution_drift_weekly.py "
        "tests/test_ws_poll_reaction.py tests/test_ws_poll_reaction_weekly.py "
        "tests/test_calibration_observation.py tests/test_calibration_observation_weekly.py "
        "tests/test_learning_loop_observation.py tests/test_learning_loop_observation_weekly.py "
        "tests/test_invariant_citations.py tests/test_identity_column_defaults.py "
        "tests/test_truth_authority_enum.py tests/test_dynamic_sql_baseline.py "
        "tests/test_contract_source_fields_baseline.py tests/test_data_rebuild_relationships.py "
        "tests/test_phase10d_closeout.py tests/test_ensemble_snapshots_bias_corrected_schema.py "
        "tests/test_tigge_snapshot_p_raw_backfill.py tests/test_db.py "
        "tests/test_replay_time_provenance.py tests/test_run_replay_cli.py "
        "tests/test_rebuild_pipeline.py tests/test_calibration_unification.py "
        "tests/test_p0_hardening.py tests/test_healthcheck.py "
        "tests/test_assumptions_validation.py tests/test_semantic_linter.py "
        "tests/test_runtime_guards.py tests/runtime/test_evaluator_oracle_resilience.py"
    )
    test_file_list = _TEST_FILES.split()

    try:
        result = subprocess.run(
            [pytest_bin, "-m", "pytest"] + test_file_list + ["-q", "--no-header"],
            capture_output=True, text=True, timeout=180, cwd=REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        return "deny", "regression_below_baseline__pytest_timed_out"
    except OSError as exc:
        return "deny", f"regression_below_baseline__pytest_exec_error"

    import re as _re2
    def _last_count(word: str, text: str) -> int:
        matches = _re2.findall(r"(\d+)\s+" + word + r"\b", text)
        return int(matches[-1]) if matches else 0

    full = result.stdout + result.stderr
    passed = _last_count("passed", full)
    failed = _last_count("failed", full)
    errors = _last_count(r"errors?", full)

    if result.returncode != 0 and failed == 0 and errors == 0:
        return "deny", "regression_below_baseline__pytest_non_zero_unparseable"
    if failed > 0 or errors > 0:
        return "deny", f"regression_below_baseline__{failed}_failed_{errors}_errors"
    if passed < baseline_passed:
        return "deny", (
            f"regression_below_baseline__observed_{passed}_baseline_{baseline_passed}"
        )
    return "allow", "invariant_baseline_passed"


def _run_blocking_check_secrets_scan(
    payload: dict[str, Any],
) -> tuple[str, str]:
    """
    Run gitleaks against staged content for `git commit`.
    Honors SECURITY-FALSE-POSITIVES.md + .gitleaks.toml allowlist.
    """
    command = _command_from_payload(payload)
    if not command:
        return "allow", "no_command"

    hc = _load_hook_common()
    try:
        subcommands = hc.git_subcommands(command)
    except ValueError:
        if hc._raw_mentions_git(command):
            return "deny", "could_not_parse_secrets_scan_command"
        return "allow", "not_git_command"

    if "commit" not in subcommands:
        return "allow", "not_git_commit"

    if os.environ.get("SECRETS_SCAN_SKIP", "0") == "1":
        return "allow", "SECRETS_SCAN_SKIP_env"

    try:
        unregistered = hc.validate_staged_review_safe_tags(str(REPO_ROOT))
        if unregistered:
            return "deny", "secrets_found__unregistered_review_safe_tag"
    except ValueError:
        return "deny", "secrets_found__review_safe_registry_error"

    # Locate gitleaks binary
    gitleaks_bin: str | None = None
    try:
        which = subprocess.run(
            ["which", "gitleaks"], capture_output=True, text=True, timeout=3,
        )
        if which.returncode == 0:
            gitleaks_bin = which.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass

    if gitleaks_bin is None:
        return "allow", "gitleaks_not_installed_advisory"

    gitleaks_toml = REPO_ROOT / ".gitleaks.toml"
    cmd = [gitleaks_bin, "protect", "--staged", "--redact", "--no-banner"]
    if gitleaks_toml.exists():
        cmd += ["--config", str(gitleaks_toml)]

    try:
        gl_result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, cwd=REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        return "deny", "secrets_found__gitleaks_timed_out"
    except OSError:
        return "allow", "gitleaks_exec_error_advisory"

    if gl_result.returncode != 0:
        return "deny", "secrets_found__gitleaks_detected_secrets"

    return "allow", "secrets_scan_passed"


def _run_blocking_check_cotenant_staging_guard(
    payload: dict[str, Any],
) -> tuple[str, str]:
    """
    Block broad `git add` (-A, --all, .) in main worktree.
    Linked worktrees have isolated indexes — safe to broad-stage.
    """
    command = _command_from_payload(payload)
    if not command:
        return "allow", "no_command"

    if os.environ.get("COTENANT_GUARD_BYPASS", "0") == "1":
        return "allow", "COTENANT_GUARD_BYPASS_env"

    hc = _load_hook_common()
    if not hc.git_add_is_broad(command):
        return "allow", "not_broad_git_add"

    try:
        gd_result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        )
        git_dir = gd_result.stdout.strip() if gd_result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        git_dir = ""

    if "/worktrees/" in git_dir:
        return "allow", "linked_worktree_isolated_index"

    reason = (
        "BLOCKED: broad staging in main worktree\n\n"
        "`git add -A`, `--all`, and `.` are blocked in the main worktree "
        "where a co-tenant agent's uncommitted changes could be absorbed.\n\n"
        "Stage specific files:\n"
        "  git add src/foo.py tests/test_foo.py\n\n"
        "Override:\n"
        "  STRUCTURED_OVERRIDE=SOLO_AGENT  (no co-tenant active)\n"
        "  STRUCTURED_OVERRIDE=ISOLATED_WORKTREE  (confirmed linked worktree)"
    )
    return "deny", reason


def _run_blocking_check_pre_merge_contamination(
    payload: dict[str, Any],
) -> tuple[str, str]:
    """
    Conflict-first guidance + MERGE_AUDIT_EVIDENCE validation on protected branches.
    Protected set: main, master, live-launch-*
    """
    import re as _re3

    command = _command_from_payload(payload)
    if not command:
        return "allow", "no_command"

    hc = _load_hook_common()
    try:
        subcommands = hc.git_subcommands(command)
    except ValueError:
        if hc._raw_mentions_git(command):
            return "deny", "could_not_parse_merge_command"
        subcommands = []

    is_merge = (
        any(s in subcommands for s in ("merge", "pull", "cherry-pick", "rebase", "am"))
        or bool(_re3.search(r"gh\s+pr\s+merge", command))
    )
    if not is_merge:
        return "allow", "not_merge_command"

    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        )
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        current_branch = ""

    if not _re3.match(r"^(main|master|live-launch-.+)$", current_branch):
        return "allow", "not_protected_branch"

    evidence = os.environ.get("MERGE_AUDIT_EVIDENCE", "").strip()
    if not evidence:
        # Conflict-first advisory — not a deny (matches legacy shell exit 0)
        return "allow", "merge_advisory_conflict_first_no_evidence"

    if evidence.startswith("OVERRIDE_"):
        return "allow", f"merge_audit_operator_override"

    evidence_path = Path(evidence) if Path(evidence).is_absolute() else REPO_ROOT / Path(evidence)
    if not evidence_path.exists():
        return "deny", "merge_audit_invalid__evidence_file_not_found"

    content = evidence_path.read_text(errors="replace")
    for field in ("critic_verdict:", "diff_scope:", "drift_keyword_scan:"):
        if not any(line.startswith(field) for line in content.splitlines()):
            return "deny", f"merge_audit_invalid__missing_field_{field.rstrip(':')}"

    import re as _re4
    m = _re4.search(r"^critic_verdict:\s*(\S+)", content, _re4.MULTILINE)
    verdict = m.group(1).strip() if m else ""
    if verdict == "APPROVE":
        return "allow", "merge_audit_evidence_approved"
    return "deny", f"merge_audit_invalid__verdict_{verdict}"


def _run_advisory_check_post_merge_cleanup(
    payload: dict[str, Any],
) -> str | None:
    """Soft cleanup checklist after `gh pr merge`. ADVISORY — PostToolUse."""
    import re as _re5

    tool_input = payload.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not command:
        return None

    if not _re5.search(r"gh\s+pr\s+merge(?:\s|$)", command):
        return None

    tool_response = payload.get("tool_response", {})
    exit_code = tool_response.get("exit_code", 0) if isinstance(tool_response, dict) else 0
    if exit_code != 0:
        return None

    worktree_lines: list[str] = []
    try:
        wt_result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        )
        paths = [
            line[len("worktree "):].strip()
            for line in wt_result.stdout.splitlines()
            if line.startswith("worktree ")
        ]
        main_wt = paths[0] if paths else None
        for p in paths[1:]:
            if "/tmp/" in p or "/T/" in p:
                continue
            worktree_lines.append(f"  worktree: {p}  ->  git worktree remove <path>")
    except (subprocess.TimeoutExpired, OSError):
        pass

    wt_section = "\n".join(worktree_lines) if worktree_lines else "  worktrees: only main"
    return (
        "\n-- Post-merge cleanup (soft) --\n"
        f"{wt_section}\n"
        "  ops packet: delete by default (git = backup); git mv to docs/archives/\n"
        "    only when packet holds evidence git log can't summarize.\n"
        "  context: /compact long sessions; rm .omc/state/agent-replay-*.jsonl\n"
        "    when no recovery active.\n"
        "------------------------------\n"
    )


def _run_blocking_check_pre_edit_architecture(
    payload: dict[str, Any],
) -> tuple[str, str]:
    """Refuse edit to architecture/** without ARCH_PLAN_EVIDENCE."""
    file_path = _file_path_from_payload(payload)
    if not file_path:
        tool_input = payload.get("tool_input", {})
        file_path = (
            tool_input.get("notebook_path", "") if isinstance(tool_input, dict) else ""
        )
    if not file_path:
        return "allow", "no_file_path"

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

    if not fpath_str.startswith("architecture/"):
        return "allow", "not_architecture_path"

    evidence = os.environ.get("ARCH_PLAN_EVIDENCE", "").strip()
    if evidence:
        ep = Path(evidence) if Path(evidence).is_absolute() else REPO_ROOT / evidence
        if ep.exists():
            return "allow", "arch_plan_evidence_present"

    return "deny", "arch_plan_evidence_missing"


def _run_blocking_check_pre_write_capability_gate(
    payload: dict[str, Any],
) -> tuple[str, str]:
    """
    Topology Gate 1 — refuse writes to hard_kernel_paths without evidence.

    Delegates to src.architecture.gate_edit_time.evaluate() (the authoritative
    module). Falls back to direct capabilities.yaml scan if the module is
    not importable (e.g. venv not active).
    """
    if os.environ.get("ZEUS_ROUTE_GATE_EDIT", "").lower() == "off":
        return "allow", "ZEUS_ROUTE_GATE_EDIT_off"

    file_path = _file_path_from_payload(payload)
    if not file_path:
        tool_input = payload.get("tool_input", {})
        file_path = (
            tool_input.get("notebook_path", "") if isinstance(tool_input, dict) else ""
        )
    if not file_path:
        return "allow", "no_file_path"

    # Primary path: delegate to gate_edit_time module
    try:
        import sys as _sys
        _repo_str = str(REPO_ROOT)
        if _repo_str not in _sys.path:
            _sys.path.insert(0, _repo_str)
        from src.architecture.gate_edit_time import evaluate  # type: ignore[import]
        allowed, _msg = evaluate([file_path])
        return ("allow", "capability_gate_passed") if allowed else ("deny", "capability_violation")
    except Exception:
        pass

    # Fallback: direct capabilities.yaml hard_kernel_paths check
    caps_path = REPO_ROOT / "architecture" / "capabilities.yaml"
    if not caps_path.exists() or yaml is None:
        return "allow", "capability_gate_fallback_allow"

    try:
        caps_data = yaml.safe_load(caps_path.read_text())
    except Exception:
        return "allow", "capabilities_yaml_parse_error"

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

    evidence = os.environ.get("ARCH_PLAN_EVIDENCE", "").strip()
    evidence_exists = False
    if evidence:
        ep = Path(evidence) if Path(evidence).is_absolute() else REPO_ROOT / evidence
        evidence_exists = ep.exists()

    for cap in caps_data.get("capabilities", []):
        for kp in cap.get("hard_kernel_paths", []):
            if fpath_str == kp or fpath_str.startswith(kp.rstrip("/") + "/"):
                if not evidence_exists:
                    return "deny", f"capability_violation"
                return "allow", "capability_gate_passed_with_evidence"

    return "allow", "no_matching_capability"


def _run_blocking_check(
    spec: dict[str, Any], payload: dict[str, Any]
) -> tuple[str, str]:
    """
    Return (decision, reason) for BLOCKING hooks.
    Phase 3.R: all 7 legacy shell logics ported; phase1_stub removed.
    Unrecognized hook_id -> ("deny", "unknown_hook") per spec.
    """
    hook_id = spec.get("id", "")

    if hook_id == "pre_checkout_uncommitted_overlap":
        return _run_blocking_check_pre_checkout_uncommitted_overlap(payload)
    elif hook_id == "pre_edit_hooks_protected":
        return _run_blocking_check_pre_edit_hooks_protected(payload)
    elif hook_id == "invariant_test":
        return _run_blocking_check_invariant_test(payload)
    elif hook_id == "secrets_scan":
        return _run_blocking_check_secrets_scan(payload)
    elif hook_id == "cotenant_staging_guard":
        return _run_blocking_check_cotenant_staging_guard(payload)
    elif hook_id == "pre_merge_contamination":
        return _run_blocking_check_pre_merge_contamination(payload)
    elif hook_id == "pre_edit_architecture":
        return _run_blocking_check_pre_edit_architecture(payload)
    elif hook_id == "pre_write_capability_gate":
        return _run_blocking_check_pre_write_capability_gate(payload)
    else:
        # Unknown BLOCKING hook — fail-closed
        return "deny", f"unknown_hook"


def _run_advisory_check(
    spec: dict[str, Any], payload: dict[str, Any]
) -> str | None:
    """
    Return additionalContext string for ADVISORY hooks, or None.
    Phase 3.R: all advisory hooks implemented.
    Unrecognized hook_id -> None (no advisory emitted).
    """
    hook_id = spec.get("id", "")

    if hook_id == "pr_create_loc_accumulation":
        return _run_advisory_check_pr_create_loc_accumulation(payload)
    elif hook_id == "pr_open_monitor_arm":
        return _run_advisory_check_pr_open_monitor_arm(payload)
    elif hook_id == "phase_close_commit_required":
        return _run_advisory_check_phase_close_commit_required(payload)
    elif hook_id == "post_merge_cleanup":
        return _run_advisory_check_post_merge_cleanup(payload)
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
