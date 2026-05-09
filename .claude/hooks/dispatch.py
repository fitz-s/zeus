#!/usr/bin/env python3
# Created: 2026-05-06
# Last reused or audited: 2026-05-07
# Authority basis: docs/operations/task_2026-05-07_hook_redesign_v2/PLAN.md
#   v2 minimal: BLOCKING tier retired, bespoke authorization removed.
#   All hooks ADVISORY-only. Boot self-test added (K1).

"""
dispatch.py -- single entry point for every Claude Code hook event.

All hooks are ADVISORY: fail-open on any unhandled exception (exit 0).
No deny path exists. Authorization = Claude Code permission prompt (default)
or user bypass choice. No env-var override dance.

Boot self-test (K1): on import, validates every hook in registry.yaml has a
matching _run_advisory_check_<id> symbol. Missing symbol -> stderr warning,
that hook falls open. Never raises.
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
LOG_DIR = REPO_ROOT / ".claude" / "logs" / "hook_signal"

# Sentinel returned by a blocking check to signal the main dispatcher to exit 2.
# The blocking check writes the human-readable reason to stderr before returning this.
_BLOCK_SENTINEL = "__BLOCK__"


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML not installed; cannot load hook registry.")
    return yaml.safe_load(path.read_text())


def _load_registry() -> dict[str, Any]:
    return _load_yaml(REGISTRY_PATH)


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def _emit_signal(
    hook_id: str,
    event: str,
    decision: str,
    reason: str,
    payload: dict[str, Any],
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
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with log_file.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# JSON output helper
# ---------------------------------------------------------------------------


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
# Per-hook advisory check implementations
# ---------------------------------------------------------------------------


def _command_from_payload(payload: dict[str, Any]) -> str:
    """Extract command string from hook payload tool_input."""
    return payload.get("tool_input", {}).get("command", "")


def _file_path_from_payload(payload: dict[str, Any]) -> str:
    """Extract file_path from Edit/Write/MultiEdit/NotebookEdit payload."""
    tool_input = payload.get("tool_input", {})
    return tool_input.get("file_path", "") or tool_input.get("path", "")


def _run_advisory_check_invariant_test(
    payload: dict[str, Any],
) -> str | None:
    """Advisory: emit reminder context when git commit is detected."""
    command = _command_from_payload(payload)
    if not command:
        return None
    import re
    if not re.search(r"\bgit\b.*\bcommit\b", command):
        return None
    return (
        "ADVISORY: git commit detected. Invariant tests (pytest baseline) "
        "should pass before committing. Run: .venv/bin/python -m pytest "
        "<invariant test files> -q --no-header"
    )


def _run_advisory_check_secrets_scan(
    payload: dict[str, Any],
) -> str | None:
    """Advisory: emit reminder context when git commit is detected."""
    command = _command_from_payload(payload)
    if not command:
        return None
    import re
    if not re.search(r"\bgit\b.*\bcommit\b", command):
        return None
    return (
        "ADVISORY: git commit detected. Staged content will be scanned by "
        "gitleaks if available. Ensure no secrets are staged."
    )


def _run_advisory_check_cotenant_staging_guard(
    payload: dict[str, Any],
) -> str | None:
    """Advisory: warn on broad git add in main worktree."""
    command = _command_from_payload(payload)
    if not command:
        return None
    import re
    if not re.search(r"\bgit\s+add\b", command):
        return None
    if not re.search(r"(\s-A|\s--all|\s\.\s*$|\s\.$)", command):
        return None
    try:
        gd = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        )
        if "/worktrees/" in gd.stdout:
            return None  # linked worktree -- isolated index, safe
    except (subprocess.TimeoutExpired, OSError):
        pass
    return (
        "ADVISORY: broad `git add` in main worktree may absorb a co-tenant "
        "agent's uncommitted changes. Prefer staging specific files: "
        "`git add src/foo.py tests/test_foo.py`"
    )


def _run_advisory_check_pre_checkout_uncommitted_overlap(
    payload: dict[str, Any],
) -> str | None:
    """Advisory: warn on git checkout/switch when tracked modifications exist."""
    command = _command_from_payload(payload)
    if not command:
        return None
    import re
    m = re.search(
        r"(?:^|[;&|]\s*)(?:/\S*/)?git\s+(?:checkout|switch)\s+([^\s;&|]+)",
        command,
    )
    if not m:
        return None
    target = m.group(1)
    if target.startswith("-") or target in ("--", "HEAD"):
        return None
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        )
        modified = [f.strip() for f in diff.stdout.splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return None
    if not modified:
        return None
    n = len(modified)
    return (
        f"ADVISORY: `git checkout {target}` with {n} tracked modification(s) "
        "uncommitted. Silently reverting tracked work is possible. "
        "Consider: `git stash push` or `git commit -m \'WIP\'` first."
    )


def _agent_authored_loc_in_range(merge_base: str, head: str) -> tuple[int, int, int]:
    """Compute (total_loc, self_authored_loc, commit_count) for the range merge_base..head.

    self_authored_loc subtracts carry-over commits whose body lacks a
    `Co-Authored-By: Claude` line — those are operator's local-main commits
    the agent's branch picked up at branch-time, not agent contribution.
    The hook uses self_authored_loc for the threshold decision so the agent
    is judged on its own work, not on what its branch dragged along.
    See architecture/agent_pr_discipline_2026_05_09.md.
    """
    import re
    try:
        commit_count = int(subprocess.run(
            ["git", "rev-list", "--count", f"{merge_base}..{head}"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        ).stdout.strip() or "0")
    except (subprocess.TimeoutExpired, ValueError, OSError):
        commit_count = 0

    try:
        shortstat = subprocess.run(
            ["git", "diff", "--shortstat", f"{merge_base}..{head}"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        ).stdout
        ins = re.findall(r"(\d+)\s+insertion", shortstat)
        dels = re.findall(r"(\d+)\s+deletion", shortstat)
        total_loc = (int(ins[-1]) if ins else 0) + (int(dels[-1]) if dels else 0)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        total_loc = 0

    self_loc = total_loc
    try:
        log = subprocess.run(
            ["git", "log", f"{merge_base}..{head}", "--format=%H%x1f%B%x1e"],
            capture_output=True, text=True, timeout=10, cwd=REPO_ROOT,
        ).stdout
        agent_shas: list[str] = []
        carry_shas: list[str] = []
        for entry in log.split("\x1e"):
            entry = entry.strip()
            if not entry or "\x1f" not in entry:
                continue
            sha, body = entry.split("\x1f", 1)
            sha = sha.strip()
            if not sha:
                continue
            # Restrict match to the actual trailer section (last paragraph).
            # git-interpret-trailers treats the last blank-line-separated
            # paragraph as the trailer block (RFC 5322 / git convention).
            # Searching the full body misclassifies operator commits that
            # quote "Co-Authored-By: Claude" in discussion text.
            paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
            trailer_block = paragraphs[-1] if paragraphs else ""
            trailer_lines = [ln.strip() for ln in trailer_block.splitlines()]
            is_agent = any(
                ln.startswith("Co-Authored-By:") and "Claude" in ln
                for ln in trailer_lines
            )
            if is_agent:
                agent_shas.append(sha)
            else:
                carry_shas.append(sha)
        # If we found a mix, recompute self_loc as the sum of agent-only commits.
        if agent_shas and carry_shas:
            self_loc = 0
            for sha in agent_shas:
                stat = subprocess.run(
                    ["git", "show", "--shortstat", "--format=", sha],
                    capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
                ).stdout
                ins = re.findall(r"(\d+)\s+insertion", stat)
                dels = re.findall(r"(\d+)\s+deletion", stat)
                self_loc += (int(ins[-1]) if ins else 0) + (int(dels[-1]) if dels else 0)
        elif not agent_shas:
            # Pure carry-over (no agent commits) — agent contribution is zero.
            self_loc = 0
        # else: pure agent work — self_loc == total_loc, leave it.
    except (subprocess.TimeoutExpired, OSError):
        # Fall back to total_loc on git failure rather than block falsely.
        pass

    return total_loc, self_loc, commit_count


def _run_advisory_check_pr_create_loc_accumulation(
    payload: dict[str, Any],
) -> str | None:
    """
    BLOCKING when self-authored LOC < 300 since merge-base.
    Bypass: set ZEUS_PR_ALLOW_TINY=1 to degrade to advisory-only.

    The block message is intentionally long. It explains the cost
    economics so the agent can reason about whether to continue
    accumulating, bundle adjacent work, or document a bypass — rather
    than discovering the rule by trial-and-error and treating it as
    an obstacle to route around. See architecture/agent_pr_discipline_2026_05_09.md.
    """
    command = _command_from_payload(payload)
    if not command:
        return None

    import re
    # Anchored to command head: optional leading inline VAR=val or `env VAR=val` pairs,
    # then gh pr create|ready. Catches both `env VAR=val gh pr create` AND the more
    # common inline form `VAR=val gh pr create`.
    if not re.search(
        r"^\s*(?:(?:env\s+)?[A-Z_][A-Z0-9_]*=\S+\s+)*gh\s+pr\s+(create|ready)\b",
        command,
    ):
        return None

    bypass_active = os.environ.get("ZEUS_PR_ALLOW_TINY", "").strip() == "1"

    # Resolve the target branch from the (possibly already-open) PR view, falling
    # back to origin/main. Using merge-base anchors the comparison correctly even
    # when @{u} is the topic branch itself after push (Codex P2 fix carryover).
    try:
        pr_base_result = subprocess.run(
            ["gh", "pr", "view", "--json", "baseRefName", "--jq", ".baseRefName"],
            capture_output=True, text=True, timeout=10, cwd=REPO_ROOT,
        )
        if pr_base_result.returncode == 0 and pr_base_result.stdout.strip():
            target_branch = f"origin/{pr_base_result.stdout.strip()}"
        else:
            target_branch = "origin/main"
    except (subprocess.TimeoutExpired, OSError):
        target_branch = "origin/main"

    try:
        merge_base = subprocess.run(
            ["git", "merge-base", "HEAD", target_branch],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        ).stdout.strip() or target_branch
    except (subprocess.TimeoutExpired, OSError, ValueError):
        merge_base = target_branch

    total_loc, self_loc, commit_count = _agent_authored_loc_in_range(merge_base, "HEAD")

    try:
        push_count = sum(
            1 for line in subprocess.run(
                ["git", "reflog", "show", "--pretty=%gD", "HEAD"],
                capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
            ).stdout.splitlines()
            if "push" in line.lower()
        )
    except (subprocess.TimeoutExpired, OSError):
        push_count = 0

    LOC_THRESHOLD = 300

    is_tiny = self_loc < LOC_THRESHOLD
    if not is_tiny:
        return None

    state_block = (
        f"   commits since base:    {commit_count}\n"
        f"   total LOC since base:  {total_loc}\n"
        f"   self-authored LOC:     {self_loc}        (threshold: {LOC_THRESHOLD})\n"
        f"   pushes already:        {push_count}\n"
    )

    if bypass_active:
        return (
            "ADVISORY (ZEUS_PR_ALLOW_TINY bypass active): PR open with small self-authored accumulation.\n"
            f"{state_block}"
            "Document the bypass reason in the PR body so reviewers understand why the rule was suspended.\n"
            "Reference: architecture/agent_pr_discipline_2026_05_09.md"
        )

    # BLOCKING: educational message — the rule is downstream of the cost facts;
    # state the facts so the agent reasons rather than rote-bypasses.
    block_msg = (
        f"BLOCKED: `gh pr create` declined — self-authored accumulation below 300 LOC.\n"
        f"\n"
        f"Three sibling principles govern the full PR workflow (authority: architecture/agent_pr_discipline_2026_05_09.md):\n"
        f"  P2: Bot comments are bug reports; the fix-commit IS the response — no thread replies.\n"
        f"  P3: Original-executor continuity — whoever wrote the commits handles the review loop.\n"
        f"  P4: Teach reasoning, not rules — hooks are backstops; the authority doc is the spec.\n"
        f"\n"
        f"Principle 1 (this gate) — cost economics (reason through it; the rule is downstream):\n"
        f"  - Each `gh pr create` / push to an open PR triggers paid auto-reviewers (Copilot + Codex)\n"
        f"    within 5-8 min. Cost is per-fire regardless of diff size: 50-LOC pays the same as 1000-LOC.\n"
        f"  - Reviewers run on a senior model tier; spending senior cognition on tiny diffs is the same\n"
        f"    waste shape as spending opus on file-location grep work.\n"
        f"  - Bundling is dominant: ten 50-LOC PRs cost ~10x one 500-LOC PR for identical review work.\n"
        f"  - 300 LOC calibrated against observed bot-comment signal: PRs <300 LOC averaged <0.3\n"
        f"    actionable findings per fire; PRs >=300 LOC averaged >=2.\n"
        f"\n"
        f"Current state (anchor your decision in this):\n"
        f"{state_block}"
        f"\n"
        f"Decision tree:\n"
        f"  A. More related work to do? -> Continue committing on this branch; open one PR when ready.\n"
        f"  B. Genuinely isolated one-off? -> Audit the assumption (same module's other latent bugs,\n"
        f"     test file gaps, doc that should also update). If still isolated AND urgent, set\n"
        f"     ZEUS_PR_ALLOW_TINY=1 and JUSTIFY in the PR body.\n"
        f"  C. Multiple PRs queued? -> STOP. Combine. Each PR-open is a fresh fire.\n"
        f"  D. Operator said ship NOW? -> Use the bypass; cite the directive in the PR body.\n"
        f"\n"
        f"Authority: architecture/agent_pr_discipline_2026_05_09.md\n"
        f"Session memory (operator side): feedback_pr_300_loc_threshold_with_education.md,\n"
        f"  feedback_pr_unit_of_work_not_loc.md, feedback_pr_bot_comments_are_bug_reports.md,\n"
        f"  feedback_pr_original_executor_continuity.md\n"
        f"\n"
        f"Bypass: ZEUS_PR_ALLOW_TINY=1 (degrades to advisory; document reason in PR body)."
    )
    print(block_msg, file=sys.stderr)
    return _BLOCK_SENTINEL


def _run_advisory_check_pre_merge_comment_check(
    payload: dict[str, Any],
) -> str | None:
    """BLOCKING on `gh pr merge <PR#>` when:
    - PR age < 600s (auto-reviewers haven't had time to fire)
    - ANY unresolved review thread exists (B2 strict: all-threads-resolved)
    - Any review state == CHANGES_REQUESTED (and not dismissed)
    Bypass: ZEUS_PR_MERGE_FORCE=1 -> emit warning and allow.
    B2 strict mode: previously Codex P2 was advisory-only; now ALL unresolved
    threads (any author, any badge) are blocking. This PR is subject to its own
    B2 gate: every thread on this PR must be resolved before merge.
    COMMENTED state reviews never block (Copilot summary posts).
    """
    command = _command_from_payload(payload)
    if not command:
        return None

    import re
    # Anchored to word boundary: prevent false-positive on echo/heredoc containing this text
    m = re.search(r"(?:^|\s)gh\s+pr\s+merge\s+(\d+)(?:\s|$)", command)
    if not m:
        return None

    pr_num = m.group(1)
    bypass_active = os.environ.get("ZEUS_PR_MERGE_FORCE", "").strip() == "1"

    block_reasons: list[str] = []
    advisory_notes: list[str] = []
    owner: str = ""
    repo_name: str = ""

    # -- PR age gate ----------------------------------------------------------
    try:
        pr_view = subprocess.run(
            ["gh", "pr", "view", pr_num, "--json", "createdAt"],
            capture_output=True, text=True, timeout=15, cwd=REPO_ROOT,
        )
        if pr_view.returncode == 0:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            pr_data = _json.loads(pr_view.stdout)
            created_at_str = pr_data.get("createdAt", "")
            if created_at_str:
                created_at = _dt.fromisoformat(created_at_str.replace("Z", "+00:00"))
                pr_age_s = (_dt.now(_tz.utc) - created_at).total_seconds()
                if pr_age_s < 600:
                    block_reasons.append(
                        f"PR age {int(pr_age_s)}s < 600s; wait for auto-reviewers (5-8 min typical)"
                    )
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError) as _e:
        # ALLOW on probe failure (transient gh outage); log so operators can diagnose.
        print(f"[pre_merge_comment_check] PR-age probe failed ({_e!r}); allowing (fail-open).", file=sys.stderr)

    # -- GraphQL review threads (B2 strict: ALL unresolved = block) ----------
    # B2 strict mode: block on ANY unresolved review thread, regardless of author
    # or badge tier. Codex P0/P1 emit a specific label; all others get a generic
    # "unresolved thread" message. Bypass: ZEUS_PR_MERGE_FORCE=1.
    try:
        repo_result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name"],
            capture_output=True, text=True, timeout=10, cwd=REPO_ROOT,
        )
        if repo_result.returncode == 0:
            import json as _json
            repo_data = _json.loads(repo_result.stdout)
            owner = repo_data.get("owner", {}).get("login", "")
            repo_name = repo_data.get("name", "")

            gql_query = (
                "{ repository(owner: \"%s\", name: \"%s\") {"
                " pullRequest(number: %s) {"
                " reviewThreads(first: 100) { nodes { isResolved"
                " comments(first: 5) { nodes { author { login } body } } } } } } }"
            ) % (owner, repo_name, pr_num)

            gql_result = subprocess.run(
                ["gh", "api", "graphql", "-f", f"query={gql_query}"],
                capture_output=True, text=True, timeout=20, cwd=REPO_ROOT,
            )
            if gql_result.returncode == 0:
                gql_data = _json.loads(gql_result.stdout)
                threads = (
                    gql_data.get("data", {})
                    .get("repository", {})
                    .get("pullRequest", {})
                    .get("reviewThreads", {})
                    .get("nodes", [])
                )
                for thread in threads:
                    if thread.get("isResolved"):
                        continue
                    # B2 strict: every unresolved thread is a blocker
                    first_comment = (
                        thread.get("comments", {}).get("nodes", [{}])[0]
                        if thread.get("comments", {}).get("nodes")
                        else {}
                    )
                    author = (first_comment.get("author") or {}).get("login", "unknown")
                    body = first_comment.get("body", "")
                    snippet = body[:80].replace("\n", " ")
                    if author == "chatgpt-codex-connector[bot]":
                        if "P0 Badge" in body or "P1 Badge" in body:
                            block_reasons.append(
                                f"Unresolved Codex P0/P1 thread: {snippet!r}"
                            )
                        else:
                            # P2 or other Codex badge — B2 strict: block (previously advisory)
                            block_reasons.append(
                                f"Unresolved Codex thread (@{author}): {snippet!r}"
                            )
                    else:
                        block_reasons.append(
                            f"Unresolved review thread (@{author}): {snippet!r}"
                        )
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError) as _e:
        # ALLOW on probe failure; log to stderr so operators can diagnose transient issues.
        print(f"[pre_merge_comment_check] review-thread probe failed ({_e!r}); allowing (fail-open).", file=sys.stderr)

    # -- CHANGES_REQUESTED reviews --------------------------------------------
    try:
        reviews_result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo_name}/pulls/{pr_num}/reviews",
             "--jq", "[.[] | {state: .state, login: .user.login}]"],
            capture_output=True, text=True, timeout=15, cwd=REPO_ROOT,
        )
        if reviews_result.returncode == 0:
            import json as _json
            reviews = _json.loads(reviews_result.stdout or "[]")
            # Track latest state per reviewer
            latest: dict[str, str] = {}
            for rv in reviews:
                login = rv.get("login", "")
                state = rv.get("state", "")
                if login and state:
                    latest[login] = state
            for login, state in latest.items():
                if state == "CHANGES_REQUESTED":
                    block_reasons.append(
                        f"CHANGES_REQUESTED review from {login} (not dismissed)"
                    )
    except (subprocess.TimeoutExpired, OSError, ValueError) as _e:
        # ALLOW on probe failure; log to stderr so operators can diagnose.
        print(f"[pre_merge_comment_check] reviews probe failed ({_e!r}); allowing (fail-open).", file=sys.stderr)

    # -- Build response -------------------------------------------------------
    if not block_reasons and not advisory_notes:
        return None

    if block_reasons:
        block_text = (
            f"BLOCKED: gh pr merge {pr_num} — this fires when an agent skipped Principle 2\n"
            f"  (architecture/agent_pr_discipline_2026_05_09.md § Principle 2).\n"
            f"\n"
            f"Principle 2: bot comments are bug reports; the fix-commit IS the response.\n"
            f"  Hard rule: do NOT post reply text on bot threads. Classify each thread\n"
            f"  (BUG/STYLE_NIT/MISUNDERSTANDING/NOISE/OUT_OF_SCOPE), apply fixes as commits,\n"
            f"  resolve threads via resolveReviewThread mutation. The fix-commit IS the response.\n"
            f"\n"
            f"Unresolved threads blocking merge:\n"
            + "\n".join(f"  - {r}" for r in block_reasons)
            + f"\n\nFor each thread: read -> classify -> fix (commit) or dismiss -> resolve via GraphQL:\n"
            f"  gh api graphql -f query='mutation{{resolveReviewThread(input:{{threadId:\"...\"}}){{thread{{isResolved}}}}}}'\n"
            f"Then push and let bots re-fire. Merge only when unresolved count = 0.\n"
        )
        if advisory_notes:
            block_text += "\nAdvisory (non-blocking):\n" + "\n".join(
                f"  - {n}" for n in advisory_notes
            )
        if bypass_active:
            return (
                f"WARNING (ZEUS_PR_MERGE_FORCE bypass active — Principle 2 skipped):\n"
                f"  architecture/agent_pr_discipline_2026_05_09.md § Principle 2 requires\n"
                f"  every thread to be processed before merge. Bypass accepted; you MUST\n"
                f"  document per-thread disposition in the PR body:\n"
                f"    APPLIED in commit <sha> / DISMISSED <reason> / DEFERRED to issue #N\n"
                f"  Threads that would have blocked:\n"
                + "\n".join(f"  - {r}" for r in block_reasons)
                + f"\nMerge allowed via ZEUS_PR_MERGE_FORCE=1."
            )
        print(block_text, file=sys.stderr)
        return _BLOCK_SENTINEL

    # Only advisory notes, no block reasons
    return "ADVISORY: " + "; ".join(advisory_notes)


def _run_advisory_check_pr_open_monitor_arm(
    payload: dict[str, Any],
) -> str | None:
    """After successful `gh pr create` or `gh pr ready`, emit Monitor arm advisory."""
    tool_response = payload.get("tool_response", {})
    tool_input = payload.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    import re
    if not re.search(r"gh\s+pr\s+(create|ready)", command):
        return None

    output = ""
    if isinstance(tool_response, dict):
        output = tool_response.get("output", "") or ""
    elif isinstance(tool_response, str):
        output = tool_response

    pr_num: str | None = None
    m = re.search(r"#(\d+)", output) or re.search(r"pulls/(\d+)", output)
    if m:
        pr_num = m.group(1)

    from datetime import timedelta
    expiry = datetime.now(timezone.utc) + timedelta(minutes=60)
    expiry_iso = expiry.strftime("%Y-%m-%dT%H:%M:%SZ")

    if pr_num is None:
        # PR number couldn't be parsed from gh output (rare: gh stdout was empty,
        # truncated, or a non-success exit). Emit a generic advisory rather than
        # a Monitor command that would resolve to `gh pr checks ?` and fail.
        return (
            f"MONITOR_ARM_REQUIRED:unknown:{expiry_iso}\n\n"
            f"PR opened, but the PR number could not be parsed from the gh output.\n"
            f"Paid auto-reviewers (Copilot, Codex) still fire within 5-8 min.\n"
            f"Find the PR number with `gh pr list --head $(git branch --show-current) --json number`\n"
            f"and arm a Monitor on `gh pr checks <number> --json name,bucket` manually."
        )

    monitor_sentinel = f"MONITOR_ARM_REQUIRED:{pr_num}:{expiry_iso}"
    return (
        f"{monitor_sentinel}\n\n"
        f"PR opened. Paid auto-reviewers (Copilot, Codex) fire within 5-8 min.\n"
        f"Arm a Monitor that watches BOTH ci checks AND reviewer comments,\n"
        f"and that filters out the agent's own replies (otherwise the watcher\n"
        f"echoes every reply you post and produces false-positive notifications):\n\n"
        f"  ME=$(gh api user --jq .login)\n"
        f"  REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)\n"
        f"  Monitor(persistent=true,\n"
        f"          command=\"prev_checks=''; prev_comments=''; prev_reviews='';\n"
        f"                   while true; do\n"
        f"                     s=$(gh pr checks {pr_num} --json name,bucket 2>/dev/null);\n"
        f"                     cur_checks=$(jq -r '.[] | select(.bucket!=\\\"pending\\\") | \\\"\\(.name): \\(.bucket)\\\"' <<<\\\"$s\\\" | sort);\n"
        f"                     comm -13 <(echo \\\"$prev_checks\\\") <(echo \\\"$cur_checks\\\");\n"
        f"                     prev_checks=$cur_checks;\n"
        f"                     # bot/non-self comments only — exclude $ME to avoid self-reflect\n"
        f"                     cur_comments=$(gh api repos/$REPO/pulls/{pr_num}/comments \\\n"
        f"                       --jq \\\"[.[] | select(.user.login!=\\\\\\\"$ME\\\\\\\") | .id] | sort | @csv\\\" 2>/dev/null);\n"
        f"                     [ \\\"$cur_comments\\\" != \\\"$prev_comments\\\" ] && echo \\\"NEW_BOT_INLINE_COMMENTS\\\";\n"
        f"                     prev_comments=$cur_comments;\n"
        f"                     cur_reviews=$(gh api repos/$REPO/pulls/{pr_num}/reviews \\\n"
        f"                       --jq \\\"[.[] | select(.user.login!=\\\\\\\"$ME\\\\\\\") | .id] | sort | @csv\\\" 2>/dev/null);\n"
        f"                     [ \\\"$cur_reviews\\\" != \\\"$prev_reviews\\\" ] && echo \\\"NEW_BOT_REVIEW_SUMMARY\\\";\n"
        f"                     prev_reviews=$cur_reviews;\n"
        f"                     jq -e 'all(.bucket!=\\\"pending\\\")' <<<\\\"$s\\\" >/dev/null && [ -n \\\"$prev_comments\\\" ] && break;\n"
        f"                     sleep 30;\n"
        f"                   done\")\n\n"
        f"Stop when all checks resolved AND reviewer comments addressed, or 60 min idle.\n"
        f"DESIGN NOTE: the `select(.user.login != \\\"$ME\\\")` filters out the agent's\n"
        f"own reply posts. Without it, every `gh api ... -X POST .../replies` you\n"
        f"send to address a reviewer fires the watcher again — observed in session\n"
        f"2026-05-07 as repeated false-positive NEW_INLINE_COMMENTS notifications."
    )


def _run_advisory_check_phase_close_commit_required(
    payload: dict[str, Any],
) -> str | None:
    """SubagentStop: warn when phase-class subagent returns with uncommitted changes."""
    agent_type = payload.get("agent_type", "")
    if "phase_" not in agent_type.lower():
        return None

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        )
        modified = [l for l in status.stdout.splitlines()
                    if l.strip() and not l.startswith("??")]
    except (subprocess.TimeoutExpired, OSError):
        return None

    if not modified:
        return None

    n = len(modified)
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        ).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        branch = "unknown"

    return (
        f"ADVISORY: phase subagent returned with {n} tracked modification(s) and\n"
        f"potentially zero commits during this phase (branch: {branch}).\n"
        f"Per feedback_commit_per_phase_or_lose_everything.md:\n\n"
        f"   git add <phase-N specific paths>\n"
        f"   git commit -m \'phase N close: <one-line summary>\'\n\n"
        f"Skip only if this phase deliberately accumulates into a later commit."
    )


def _run_advisory_check_pre_merge_contamination(
    payload: dict[str, Any],
) -> str | None:
    """Advisory on merge commands targeting protected branches."""
    import re as _re

    command = _command_from_payload(payload)
    if not command:
        return None

    is_merge = bool(_re.search(
        r"\bgit\s+(merge|pull|cherry-pick|rebase|am)\b|gh\s+pr\s+merge",
        command,
    ))
    if not is_merge:
        return None

    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        ).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return None

    if not _re.match(r"^(main|master|live-launch-.+)$", branch):
        return None

    return (
        "ADVISORY: merge/pull onto protected branch detected. "
        "Ensure conflicts are resolved and critic review is complete "
        "before merging. Check: git diff --name-only HEAD MERGE_HEAD"
    )


def _run_advisory_check_post_merge_cleanup(
    payload: dict[str, Any],
) -> str | None:
    """Soft cleanup checklist after `gh pr merge`."""
    import re as _re

    tool_input = payload.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not _re.search(r"gh\s+pr\s+merge(?:\s|$)", command):
        return None

    tool_response = payload.get("tool_response", {})
    exit_code = tool_response.get("exit_code", 0) if isinstance(tool_response, dict) else 0
    if exit_code != 0:
        return None

    worktree_lines: list[str] = []
    try:
        wt = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        )
        paths = [
            line[len("worktree "):].strip()
            for line in wt.stdout.splitlines()
            if line.startswith("worktree ")
        ]
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
        "    only when packet holds evidence git log cannot summarize.\n"
        "  context: /compact long sessions; rm .omc/state/agent-replay-*.jsonl\n"
        "    when no recovery active.\n"
        "------------------------------\n"
    )


def _run_advisory_check_pre_edit_architecture(
    payload: dict[str, Any],
) -> str | None:
    """Advisory on edits to architecture/** without ARCH_PLAN_EVIDENCE."""
    file_path = _file_path_from_payload(payload)
    if not file_path:
        tool_input = payload.get("tool_input", {})
        file_path = tool_input.get("notebook_path", "") if isinstance(tool_input, dict) else ""
    if not file_path:
        return None

    try:
        fpath = Path(file_path)
        if fpath.is_absolute():
            try:
                fpath = fpath.relative_to(REPO_ROOT)
            except ValueError:
                return None
        if not fpath.as_posix().startswith("architecture/"):
            return None
    except Exception:
        return None

    evidence = os.environ.get("ARCH_PLAN_EVIDENCE", "").strip()
    if evidence:
        ep = Path(evidence) if Path(evidence).is_absolute() else REPO_ROOT / evidence
        if ep.exists():
            return None

    return (
        "ADVISORY: editing architecture/** without ARCH_PLAN_EVIDENCE. "
        "Ensure an architecture plan exists before modifying capability definitions."
    )


def _run_advisory_check_pre_write_capability_gate(
    payload: dict[str, Any],
) -> str | None:
    """Advisory on writes to blocking-class capability paths."""
    if os.environ.get("ZEUS_ROUTE_GATE_EDIT", "").lower() == "off":
        return None

    file_path = _file_path_from_payload(payload)
    if not file_path:
        tool_input = payload.get("tool_input", {})
        file_path = tool_input.get("notebook_path", "") if isinstance(tool_input, dict) else ""
    if not file_path:
        return None

    try:
        import sys as _sys
        _repo_str = str(REPO_ROOT)
        if _repo_str not in _sys.path:
            _sys.path.insert(0, _repo_str)
        from src.architecture.gate_edit_time import evaluate  # type: ignore[import]
        allowed, msg = evaluate([file_path])
        if not allowed:
            return f"ADVISORY: capability gate would block this write: {msg}"
        return None
    except Exception:
        pass

    return None


_WORKTREE_DOCTOR = REPO_ROOT / "scripts" / "worktree_doctor.py"


def _run_advisory_check_session_start_visibility(
    payload: dict[str, Any],
) -> str | None:
    """SessionStart: invoke worktree_doctor --cross-worktree-visibility."""
    hook_id = "session_start_visibility"
    event = payload.get("hook_event_name", "SessionStart")
    try:
        result = subprocess.run(
            [sys.executable, str(_WORKTREE_DOCTOR), "--cross-worktree-visibility"],
            capture_output=True, text=True, timeout=10, cwd=REPO_ROOT,
        )
        if result.returncode != 0 or not result.stdout.strip():
            _emit_signal(hook_id, event, "error", "dispatch_error:worktree_doctor_nonzero", payload)
            return None
        ctx = result.stdout.strip()
        if len(ctx) > 1500:
            ctx = ctx[:1500] + "\n... (truncated)"
        return ctx
    except Exception as exc:
        _emit_signal(hook_id, event, "error", f"dispatch_error:{exc}", payload)
        return None


def _run_advisory_check_worktree_create_advisor(
    payload: dict[str, Any],
) -> str | None:
    """WorktreeCreate: emit naming/scope/sentinel advisory."""
    hook_id = "worktree_create_advisor"
    event = payload.get("hook_event_name", "WorktreeCreate")
    try:
        tool_input = payload.get("tool_input", {}) or {}
        wt_path = (
            tool_input.get("path", "")
            or tool_input.get("worktree_path", "")
            or payload.get("worktree_path", "")
        )
        lines = [
            "[worktree_doctor] WorktreeCreate advisory:",
            "  naming: use descriptive slug, e.g. zeus-<task-slug>-<YYYY-MM-DD>",
            "  scope: ONE task per worktree; commit per phase before switching",
            "  sentinel: write zeus_worktree.yaml at worktree root (PLAN §2.7)",
            "    fields: name, path, branch, base, agent_class, mode, task_slug, intent",
            "  isolation: DO NOT touch other worktrees from this session",
            "  capability: cross_worktree_visibility (architecture/capabilities.yaml)",
        ]
        if wt_path and Path(wt_path).exists():
            _write_worktree_sentinel_from_payload(wt_path, payload)
            lines.append(f"  sentinel written: {wt_path}/zeus_worktree.yaml")
        return "\n".join(lines)
    except Exception as exc:
        _emit_signal(hook_id, event, "error", f"dispatch_error:{exc}", payload)
        return None


def _write_worktree_sentinel_from_payload(wt_path: str, payload: dict[str, Any]) -> None:
    """Write zeus_worktree.yaml sentinel inline."""
    try:
        import yaml as _yaml
        tool_input = payload.get("tool_input", {}) or {}
        branch = tool_input.get("branch", Path(wt_path).name)
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=wt_path,
        ).stdout.strip()
        data = {
            "schema_version": 1,
            "worktree": {
                "name": Path(wt_path).name,
                "path": wt_path,
                "branch": branch,
                "base": f"main@{head}",
                "agent_class": "claude_code",
                "mode": "write",
                "task_slug": tool_input.get("task_slug", "unknown"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "intent": tool_input.get("intent", ""),
            },
            "sunset_date": "2026-08-07",
        }
        (Path(wt_path) / "zeus_worktree.yaml").write_text(
            _yaml.dump(data, default_flow_style=False)
        )
    except Exception:
        pass


def _run_advisory_check_worktree_remove_advisor(
    payload: dict[str, Any],
) -> str | None:
    """WorktreeRemove: pre-remove dirty/uncommitted check + branch closure suggestion."""
    hook_id = "worktree_remove_advisor"
    event = payload.get("hook_event_name", "WorktreeRemove")
    try:
        tool_input = payload.get("tool_input", {}) or {}
        wt_path = (
            tool_input.get("path", "")
            or tool_input.get("worktree_path", "")
            or payload.get("worktree_path", "")
        )
        lines = ["[worktree_doctor] WorktreeRemove advisory:"]
        if wt_path and Path(wt_path).exists():
            try:
                dirty = subprocess.run(
                    ["git", "status", "--short", "--porcelain"],
                    capture_output=True, text=True, timeout=5, cwd=Path(wt_path),
                )
                if dirty.stdout.strip():
                    lines.append("  WARNING: worktree has uncommitted changes -- commit or stash first")
                    lines.append("  per feedback_commit_per_phase_or_lose_everything.md")
                else:
                    lines.append("  dirty: false -- safe to remove")
            except (subprocess.TimeoutExpired, OSError):
                lines.append("  dirty: unknown (could not check)")
            try:
                branch = subprocess.run(
                    ["git", "branch", "--show-current"],
                    capture_output=True, text=True, timeout=5, cwd=Path(wt_path),
                ).stdout.strip()
                if branch and branch != "main":
                    ahead = int(subprocess.run(
                        ["git", "rev-list", "--count", f"origin/main..{branch}"],
                        capture_output=True, text=True, timeout=5, cwd=Path(wt_path),
                    ).stdout.strip() or "0")
                    if ahead > 0:
                        lines.append(f"  WARNING: {ahead} commits ahead of origin/main not in a PR")
                        lines.append("  suggest: open PR or push branch before removing worktree")
                    lines.append(f"  branch closure: after removal, `git branch -d {branch}` if merged")
            except (subprocess.TimeoutExpired, ValueError, OSError):
                pass
        lines.append("  NEVER auto-deletes (per feedback_commit_per_phase_or_lose_everything.md)")
        return "\n".join(lines)
    except Exception as exc:
        _emit_signal(hook_id, event, "error", f"dispatch_error:{exc}", payload)
        return None


# ---------------------------------------------------------------------------
# Advisory check dispatcher
# ---------------------------------------------------------------------------

# Map hook_id -> advisory check function
_ADVISORY_HANDLERS: dict[str, Any] = {
    "invariant_test": _run_advisory_check_invariant_test,
    "secrets_scan": _run_advisory_check_secrets_scan,
    "cotenant_staging_guard": _run_advisory_check_cotenant_staging_guard,
    "pre_checkout_uncommitted_overlap": _run_advisory_check_pre_checkout_uncommitted_overlap,
    "pr_create_loc_accumulation": _run_advisory_check_pr_create_loc_accumulation,
    "pre_merge_comment_check": _run_advisory_check_pre_merge_comment_check,
    "pr_open_monitor_arm": _run_advisory_check_pr_open_monitor_arm,
    "phase_close_commit_required": _run_advisory_check_phase_close_commit_required,
    "pre_merge_contamination": _run_advisory_check_pre_merge_contamination,
    "post_merge_cleanup": _run_advisory_check_post_merge_cleanup,
    "pre_edit_architecture": _run_advisory_check_pre_edit_architecture,
    "pre_write_capability_gate": _run_advisory_check_pre_write_capability_gate,
    "session_start_visibility": _run_advisory_check_session_start_visibility,
    "worktree_create_advisor": _run_advisory_check_worktree_create_advisor,
    "worktree_remove_advisor": _run_advisory_check_worktree_remove_advisor,
}


def _run_advisory_check(
    spec: dict[str, Any], payload: dict[str, Any]
) -> str | None:
    """Return additionalContext string for this hook, or None."""
    hook_id = spec.get("id", "")
    handler = _ADVISORY_HANDLERS.get(hook_id)
    if handler is None:
        return None
    return handler(payload)


# ---------------------------------------------------------------------------
# Boot self-test (K1) -- runs on module import, never raises
# ---------------------------------------------------------------------------


def _boot_self_test() -> None:
    """
    K1: validate every hook in registry.yaml has a matching handler symbol.
    Prints to stderr. Never raises. Falls open for unrecognized hooks.
    """
    try:
        if not REGISTRY_PATH.exists():
            print(
                "[hook integrity] WARN: registry.yaml not found at "
                f"{REGISTRY_PATH}",
                file=sys.stderr,
            )
            return
        registry = _load_registry()
        hooks = registry.get("hooks", [])
        missing = []
        for hook in hooks:
            hid = hook.get("id", "")
            if hid not in _ADVISORY_HANDLERS:
                missing.append(hid)
        if missing:
            print(
                f"[hook integrity] WARN: no handler for hook id(s): "
                f"{missing} -- these will fall open (advisory)",
                file=sys.stderr,
            )
        else:
            print(
                f"[hook integrity] OK: all {len(hooks)} registry hooks have handlers",
                file=sys.stderr,
            )
    except Exception as exc:
        print(f"[hook integrity] WARN: self-test error: {exc}", file=sys.stderr)


# Run self-test at module load (not only __main__)
_boot_self_test()


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------


def main(hook_id: str) -> int:
    try:
        registry = _load_registry()
    except Exception as exc:
        print(f"dispatch.py: failed to load registry: {exc}", file=sys.stderr)
        return 0  # ADVISORY: fail-open

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

    try:
        ctx = _run_advisory_check(spec, payload)
        if ctx == _BLOCK_SENTINEL:
            # Blocking check: reason already written to stderr by the check function.
            _emit_signal(hook_id, event, "block", "blocking_check", payload)
            return 2
        _emit_signal(hook_id, event, "allow", "advisory_check", payload)
        if ctx:
            return _emit_advisory(hook_id, event, ctx)
        return 0
    except Exception as exc:
        # All hooks are ADVISORY: fail-open on crash
        _emit_signal(hook_id, event, "error", f"dispatch_crash: {exc}", payload)
        return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: dispatch.py <hook_id>", file=sys.stderr)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
