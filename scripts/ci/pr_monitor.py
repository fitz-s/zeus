#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase B.5
"""
First-principle PR monitor — universal, persistent, dedup-aware.

Emits ONE LINE per meaningful event to stdout:

    PR#NN FINDING [<author>] <path>:<line>: <body[:280]>
    PR#NN CI_FAIL <check_name>:<conclusion>
    PR#NN TERMINAL state=<MERGED|CLOSED>

Silent on:
    - my own pushes (no NEW_COMMIT event)
    - CI passing or pending (only FAILURE/TIMED_OUT/CANCELLED emit)
    - resolved review threads
    - baseline / heartbeat
    - bot reviews that have zero inline findings

Dedup is persisted across invocations in
~/.cache/zeus/pr_monitor/pr_<repo>_<NN>.json so that running this script
multiple times against the same PR does not re-emit prior findings.

Usage:
    python scripts/ci/pr_monitor.py <pr_number>
    python scripts/ci/pr_monitor.py <pr_number> --repo owner/repo
    python scripts/ci/pr_monitor.py <pr_number> --poll 90 --timeout 3600
    python scripts/ci/pr_monitor.py <pr_number> --once        # single check, no loop
    python scripts/ci/pr_monitor.py <pr_number> --reset-state # clear dedup state first
    python scripts/ci/pr_monitor.py <pr_number> --json        # emit JSON instead of human lines

Requires `gh` CLI authenticated to the target repo.

Exit codes:
    0 — terminal state reached (MERGED or CLOSED), or --once completed
    1 — timeout reached before terminal
    2 — gh CLI error (auth, network, missing repo)

Authority basis:
    docs/operations/current/plans/ci_topology_refactor_refined.md Phase B.5
    Operator first-principle directive 2026-05-26: meaningful findings only,
    no self-reflection, CI failure immediate, no CI success run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

# Terminal PR states
TERMINAL_STATES = {"MERGED", "CLOSED"}

# Check conclusions that count as failures
CI_FAILURE_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE"}

# Default poll interval — 90s balances API quota with responsiveness.
DEFAULT_POLL_SECONDS = 90

# Default total timeout — 1 hour. Use --timeout 0 for no limit (loops forever).
DEFAULT_TIMEOUT_SECONDS = 3600

# Default stale-silence threshold — 900s (15 min). Operator first principle
# 2026-05-26: if monitor has been silent for ≥15 min after PR open, something
# is wrong (e.g. PR ref drift, gh CLI broken, bot reviews delayed beyond SLA,
# CI never started). Emit STALE_SILENCE to prompt direct PR check.
DEFAULT_STALE_AFTER_SECONDS = 900


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def state_dir() -> Path:
    """Returns ~/.cache/zeus/pr_monitor/ (created if missing)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base) / "zeus" / "pr_monitor"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path(repo: str, pr: int) -> Path:
    safe_repo = repo.replace("/", "_")
    return state_dir() / f"pr_{safe_repo}_{pr}.json"


def _empty_state() -> dict[str, Any]:
    return {
        "reported_threads": set(),
        "reported_failures": set(),
        # check_name → last-reported conclusion. Used to detect
        # FAILURE → SUCCESS transitions and emit CI_RECOVERED.
        # (Operator first-principle iteration 2026-05-26: silence after
        # a reported failure looked broken; recovery IS a signal.)
        "failed_checks": {},
        "reported_recoveries": set(),
        # Float Unix timestamps. None until the first event/tick.
        "last_event_at": None,
        "last_stale_emit_at": None,
        "monitor_started_at": None,
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        with path.open() as f:
            raw = json.load(f)
        return {
            "reported_threads": set(raw.get("reported_threads", [])),
            "reported_failures": set(raw.get("reported_failures", [])),
            "failed_checks": dict(raw.get("failed_checks", {})),
            "reported_recoveries": set(raw.get("reported_recoveries", [])),
            "last_event_at": raw.get("last_event_at"),
            "last_stale_emit_at": raw.get("last_stale_emit_at"),
            "monitor_started_at": raw.get("monitor_started_at"),
        }
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def save_state(path: Path, state: dict[str, Any]) -> None:
    serialized = {
        "reported_threads": sorted(state["reported_threads"]),
        "reported_failures": sorted(state["reported_failures"]),
        "failed_checks": dict(state.get("failed_checks") or {}),
        "reported_recoveries": sorted(state.get("reported_recoveries") or []),
        "last_event_at": state.get("last_event_at"),
        "last_stale_emit_at": state.get("last_stale_emit_at"),
        "monitor_started_at": state.get("monitor_started_at"),
    }
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(serialized, f, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# gh CLI wrapper
# ---------------------------------------------------------------------------


# GraphQL query — `reviewThreads` is NOT in gh's `--json` whitelist for
# `gh pr view`, so we go through `gh api graphql` directly. This was a real
# bug in the first Phase B.5 cut (Copilot PR #343 review caught silently
# broken monitor — `--json reviewThreads` returned exit 1 with stderr
# "Unknown JSON field: reviewThreads").
_GH_GRAPHQL_QUERY = """
query($owner:String!,$repo:String!,$pr:Int!){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$pr){
      state
      reviewThreads(first:100){
        nodes{
          id
          isResolved
          comments(first:1){
            nodes{
              author{login}
              path
              line
              body
            }
          }
        }
      }
      commits(last:1){
        nodes{
          commit{
            statusCheckRollup{
              contexts(first:100){
                nodes{
                  ... on CheckRun  { name conclusion status }
                  ... on StatusContext { context state }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _parse_owner_repo(repo: str) -> tuple[str, str] | None:
    """Returns (owner, repo) or None if format invalid."""
    if "/" not in repo:
        return None
    parts = repo.split("/", 1)
    if not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def gh_pr_view(pr: int, *, repo: str | None = None) -> dict[str, Any] | None:
    """
    Returns a normalized dict
        {state, reviewThreads, statusCheckRollup}
    from `gh api graphql`, or None on failure.

    The shape matches what the rest of pr_monitor.py expects (originally
    `gh pr view --json reviewThreads,statusCheckRollup,state`). gh_error
    state — caller can inspect by passing return_error=True via gh_pr_view_v2.
    For back-compat, this function returns None on any error.
    """
    pr_data, _err = gh_pr_view_v2(pr, repo=repo)
    return pr_data


def gh_pr_view_v2(
    pr: int, *, repo: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Same as gh_pr_view but also returns an error message string when gh fails.
    Used by tick_once to emit GH_ERROR events.

    Returns (data, error). Exactly one is non-None:
        (dict, None) — success
        (None, str)  — failure with human-readable reason
    """
    owner_repo = repo or _gh_default_repo()
    if not owner_repo:
        return None, "could not determine owner/repo (set --repo)"
    parts = _parse_owner_repo(owner_repo)
    if not parts:
        return None, f"invalid --repo {owner_repo!r}; expected owner/name"
    owner, repo_name = parts

    cmd = [
        "gh", "api", "graphql",
        "-f", f"query={_GH_GRAPHQL_QUERY}",
        "-f", f"owner={owner}",
        "-f", f"repo={repo_name}",
        "-F", f"pr={pr}",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
    except subprocess.TimeoutExpired:
        return None, "gh api graphql timed out after 30s"
    except FileNotFoundError:
        return None, "gh CLI not installed (`gh` not on PATH)"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip().splitlines()
        msg = stderr[0] if stderr else f"gh exit {result.returncode}"
        return None, f"gh api graphql failed: {msg}"
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return None, f"gh returned non-JSON: {e}"

    if "errors" in raw and raw["errors"]:
        first = raw["errors"][0]
        return None, f"graphql error: {first.get('message', 'unknown')}"

    try:
        pr_node = raw["data"]["repository"]["pullRequest"]
    except (KeyError, TypeError):
        return None, "graphql response missing pullRequest"
    if pr_node is None:
        return None, f"PR #{pr} not found in {owner}/{repo_name}"

    # Flatten statusCheckRollup to a list of {name, conclusion, status}
    rollup_list: list[dict[str, Any]] = []
    commits_nodes = (pr_node.get("commits") or {}).get("nodes") or []
    if commits_nodes:
        commit = commits_nodes[0].get("commit") or {}
        rollup = commit.get("statusCheckRollup") or {}
        contexts = (rollup.get("contexts") or {}).get("nodes") or []
        for c in contexts:
            if "name" in c:  # CheckRun
                rollup_list.append(
                    {"name": c["name"], "conclusion": c.get("conclusion"), "status": c.get("status")}
                )
            elif "context" in c:  # StatusContext (commit status)
                state = c.get("state")
                # Map status states to check conclusions vocabulary
                conclusion = {
                    "SUCCESS": "SUCCESS",
                    "FAILURE": "FAILURE",
                    "ERROR": "FAILURE",
                    "PENDING": None,
                    "EXPECTED": None,
                }.get(state)
                rollup_list.append(
                    {"name": c["context"], "conclusion": conclusion, "status": "COMPLETED" if conclusion else "IN_PROGRESS"}
                )

    return {
        "state": pr_node.get("state"),
        "reviewThreads": (pr_node.get("reviewThreads") or {}).get("nodes") or [],
        "statusCheckRollup": rollup_list,
    }, None


# ---------------------------------------------------------------------------
# Event extraction (first-principle filters)
# ---------------------------------------------------------------------------


def extract_unresolved_findings(pr_data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return list of unresolved review threads that have non-empty body content.
    Each: {tid, author, path, line, body}.
    """
    out: list[dict[str, Any]] = []
    for thread in pr_data.get("reviewThreads") or []:
        if thread.get("isResolved", False):
            continue
        comments = (thread.get("comments") or {}).get("nodes") or []
        if not comments:
            continue
        first = comments[0]
        body = (first.get("body") or "").strip()
        if not body:
            continue
        out.append(
            {
                "tid": thread.get("id") or "",
                "author": (first.get("author") or {}).get("login") or "?",
                "path": first.get("path") or "?",
                "line": first.get("line") or 0,
                "body": body,
            }
        )
    return out


def extract_ci_failures(pr_data: dict[str, Any]) -> list[dict[str, str]]:
    """
    Return list of currently-failing checks. Each: {name, conclusion}.
    Pending and successful checks are silently dropped.
    """
    out: list[dict[str, str]] = []
    for check in pr_data.get("statusCheckRollup") or []:
        conclusion = check.get("conclusion")
        if conclusion not in CI_FAILURE_CONCLUSIONS:
            continue
        name = check.get("name") or check.get("context") or "unknown"
        out.append({"name": name, "conclusion": conclusion})
    return out


def is_terminal(pr_data: dict[str, Any]) -> str | None:
    """Return MERGED or CLOSED if the PR is terminal, else None."""
    state = pr_data.get("state")
    if state in TERMINAL_STATES:
        return state
    return None


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


def format_finding_line(pr: int, finding: dict[str, Any]) -> str:
    body = finding["body"].replace("\n", " ").replace("\r", " ").strip()
    if len(body) > 280:
        body = body[:277] + "..."
    return (
        f"PR#{pr} FINDING [{finding['author']}] "
        f"{finding['path']}:{finding['line']}: {body}"
    )


def format_ci_fail_line(pr: int, failure: dict[str, str]) -> str:
    return f"PR#{pr} CI_FAIL {failure['name']}:{failure['conclusion']}"


def format_terminal_line(pr: int, state: str) -> str:
    return f"PR#{pr} TERMINAL state={state}"


def format_ci_recovered_line(pr: int, check_name: str, prior_conclusion: str) -> str:
    """
    Emitted when a previously-reported FAILURE check is now SUCCESS.
    Dedup by check_name — at most one recovery emission per check, per
    monitor instance (state persisted in `reported_recoveries`).
    Per first principle: signal change, not heartbeat.
    """
    return f"PR#{pr} CI_RECOVERED {check_name} (was {prior_conclusion})"


def extract_currently_passing_checks(pr_data: dict[str, Any]) -> set[str]:
    """Return names of checks whose conclusion is SUCCESS in the latest poll."""
    out: set[str] = set()
    for check in pr_data.get("statusCheckRollup") or []:
        if check.get("conclusion") == "SUCCESS":
            name = check.get("name") or check.get("context") or "unknown"
            out.add(name)
    return out


def format_gh_error_line(pr: int, error: str) -> str:
    """
    Emitted when the underlying gh CLI fails (auth, network, PR-ref drift,
    invalid GraphQL field, etc). This is meaningful — silent broken-API is
    exactly the failure mode this script is supposed to surface.
    """
    return f"PR#{pr} GH_ERROR {error}"


def format_stale_silence_line(pr: int, elapsed_seconds: int, last_event_at: float | None) -> str:
    """
    Emitted when no meaningful event has fired for >= stale_after_seconds.
    Prompts a direct PR check (something may be wrong: gh CLI down,
    PR-ref drift, CI never started, bot review SLA exceeded).
    """
    if last_event_at is None:
        last = "never"
    else:
        last = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_event_at))
    return (
        f"PR#{pr} STALE_SILENCE elapsed={elapsed_seconds}s last_event_at={last} "
        f"hint=check PR directly (gh pr view {pr})"
    )


def check_stale_silence(
    state: dict[str, Any],
    *,
    threshold_seconds: int,
    now: float | None = None,
) -> int | None:
    """
    Returns elapsed_seconds if STALE_SILENCE should be emitted now, else None.

    Rules (first-principle per operator 2026-05-26):
      - Anchor = max(last_event_at, monitor_started_at). The clock starts on
        whichever is most recent.
      - Fires when (now - anchor) >= threshold_seconds.
      - Dedup: after firing, set last_stale_emit_at = now; do not re-fire
        unless threshold_seconds passes again since last_stale_emit_at OR a
        real event resets last_event_at.
      - threshold_seconds <= 0 disables the check entirely.
    """
    if threshold_seconds <= 0:
        return None
    if now is None:
        now = time.time()

    anchors = [a for a in (state.get("last_event_at"), state.get("monitor_started_at")) if a is not None]
    if not anchors:
        # No anchor yet — caller has not initialized monitor_started_at;
        # cannot judge staleness.
        return None
    anchor = max(anchors)
    elapsed = int(now - anchor)
    if elapsed < threshold_seconds:
        return None

    last_stale = state.get("last_stale_emit_at")
    if last_stale is not None and (now - last_stale) < threshold_seconds:
        # Already emitted recently within this stale window — wait it out.
        return None

    return elapsed


def emit(line: str, *, as_json: bool = False, kind: str = "event") -> None:
    if as_json:
        print(json.dumps({"kind": kind, "line": line}))
    else:
        print(line)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Single-tick: returns new events + updates state in place
# ---------------------------------------------------------------------------


def tick_once(
    pr: int,
    *,
    repo: str | None,
    state: dict[str, Any],
    as_json: bool,
    stale_after_seconds: int = 0,
    now: float | None = None,
) -> str | None:
    """
    Pull current PR state, emit any new events, update dedup state in place.
    Returns the PR terminal state (MERGED/CLOSED) if reached, else None.

    When `stale_after_seconds > 0`, also emit STALE_SILENCE if the monitor
    has been silent for >= that many seconds (per operator first principle
    2026-05-26: ≥15 min silence = something wrong, check PR directly).
    """
    if now is None:
        now = time.time()

    pr_data, gh_error = gh_pr_view_v2(pr, repo=repo)
    emitted_any = False

    if gh_error is not None:
        # Dedup GH_ERROR by error message so repeated same-failure ticks
        # don't spam. Same dedup bucket as failures (gh:<msg>).
        key = f"gh:{gh_error}"
        if key not in state["reported_failures"]:
            emit(format_gh_error_line(pr, gh_error), as_json=as_json, kind="gh_error")
            state["reported_failures"].add(key)
            emitted_any = True
            state["last_event_at"] = now
            state["last_stale_emit_at"] = None
        # Stale check still runs below in case gh keeps failing
        elapsed = check_stale_silence(state, threshold_seconds=stale_after_seconds, now=now)
        if elapsed is not None:
            emit(
                format_stale_silence_line(pr, elapsed, state.get("last_event_at")),
                as_json=as_json,
                kind="stale_silence",
            )
            state["last_stale_emit_at"] = now
        return None

    if pr_data is not None:
        # 1. Findings — only emit if thread id not previously reported
        for finding in extract_unresolved_findings(pr_data):
            tid = finding["tid"]
            if not tid or tid in state["reported_threads"]:
                continue
            emit(format_finding_line(pr, finding), as_json=as_json, kind="finding")
            state["reported_threads"].add(tid)
            emitted_any = True

        # 2. CI failures — dedup by name:conclusion, BUT a new failure after a
        # recovery is itself meaningful: clear any prior reported_recoveries
        # entry for the same check so the next recovery can re-fire, and clear
        # stale name:conclusion dedup keys for that check (so the same failure
        # mode coming back after a pass is reported again, not silently
        # absorbed). Without this, a FAIL→PASS→FAIL cycle goes silent on the
        # second FAIL — operator-tested scene 3 antibody (2026-05-26).
        currently_failing_names = set()
        for failure in extract_ci_failures(pr_data):
            name = failure["name"]
            currently_failing_names.add(name)
            key = f"{name}:{failure['conclusion']}"
            failed_checks = state.setdefault("failed_checks", {})
            # If we previously emitted a recovery for this check and it's
            # failing again, clear the recovery dedup so the NEXT recovery
            # can re-emit, AND clear the prior reported_failures key so the
            # fresh failure re-emits.
            recoveries: set[str] = state.setdefault("reported_recoveries", set())
            if name in recoveries:
                recoveries.discard(name)
                # Drop any prior fail keys for THIS check so re-failure re-emits.
                state["reported_failures"] = {
                    k for k in state["reported_failures"]
                    if not k.startswith(f"{name}:")
                }
            failed_checks[name] = failure["conclusion"]
            if key in state["reported_failures"]:
                continue
            emit(format_ci_fail_line(pr, failure), as_json=as_json, kind="ci_fail")
            state["reported_failures"].add(key)
            emitted_any = True

        # 2b. CI_RECOVERED — previously-failed checks that are now SUCCESS.
        # First-principle iteration (operator complaint twice 2026-05-26):
        # silence after a reported failure is misread as "monitor broke" when
        # the underlying check actually recovered. Recovery IS meaningful.
        # Dedup per check; if the check fails again later, the failure branch
        # above clears the recovery dedup so a future recovery can re-emit.
        currently_passing = extract_currently_passing_checks(pr_data)
        failed_checks = state.setdefault("failed_checks", {})
        recovered_names = [
            name for name in list(failed_checks.keys())
            if name in currently_passing and name not in state.setdefault("reported_recoveries", set())
        ]
        for name in recovered_names:
            prior = failed_checks.get(name, "?")
            emit(format_ci_recovered_line(pr, name, prior),
                 as_json=as_json, kind="ci_recovered")
            state["reported_recoveries"].add(name)
            failed_checks.pop(name, None)
            emitted_any = True

        # 3. Terminal
        term = is_terminal(pr_data)
        if term:
            emit(format_terminal_line(pr, term), as_json=as_json, kind="terminal")
            state["last_event_at"] = now
            state["last_stale_emit_at"] = None
            return term

    if emitted_any:
        state["last_event_at"] = now
        # A real event resets the stale window.
        state["last_stale_emit_at"] = None

    # 4. Stale silence — must run even when gh returned None (signal that
    # the polling itself may be failing).
    elapsed = check_stale_silence(state, threshold_seconds=stale_after_seconds, now=now)
    if elapsed is not None:
        emit(
            format_stale_silence_line(pr, elapsed, state.get("last_event_at")),
            as_json=as_json,
            kind="stale_silence",
        )
        state["last_stale_emit_at"] = now

    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run(
    pr: int,
    *,
    repo: str | None,
    poll_seconds: int,
    timeout_seconds: int,
    once: bool,
    reset_state: bool,
    as_json: bool,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> int:
    repo_key = repo or _gh_default_repo() or "default"
    spath = state_path(repo_key, pr)
    if reset_state and spath.exists():
        spath.unlink()
    state = load_state(spath)

    # Anchor the stale-silence clock at first invocation (per run).
    # Without this, --reset-state + immediate stale-check would never fire,
    # and a long-idle state file would fire instantly on the first tick.
    if state.get("monitor_started_at") is None or reset_state:
        state["monitor_started_at"] = time.time()

    if once:
        tick_once(
            pr,
            repo=repo,
            state=state,
            as_json=as_json,
            stale_after_seconds=stale_after_seconds,
        )
        save_state(spath, state)
        return 0

    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    while True:
        term = tick_once(
            pr,
            repo=repo,
            state=state,
            as_json=as_json,
            stale_after_seconds=stale_after_seconds,
        )
        save_state(spath, state)
        if term is not None:
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            return 1
        time.sleep(poll_seconds)


def _gh_default_repo() -> str | None:
    """Return current repo as `owner/name` per `gh repo view`, else None."""
    try:
        out = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "First-principle PR monitor. Emits meaningful findings + CI "
            "failures + terminal only. Silent on noise."
        )
    )
    p.add_argument("pr_number", type=int, help="GitHub PR number to monitor")
    p.add_argument(
        "--repo",
        default=None,
        help="owner/repo (default: gh default for current repo)",
    )
    p.add_argument(
        "--poll",
        type=int,
        default=DEFAULT_POLL_SECONDS,
        help=f"Seconds between checks (default: {DEFAULT_POLL_SECONDS})",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            f"Total wall-clock timeout in seconds (default: "
            f"{DEFAULT_TIMEOUT_SECONDS}; 0 = no limit)"
        ),
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Single check and exit. No loop.",
    )
    p.add_argument(
        "--reset-state",
        action="store_true",
        help="Clear persisted dedup state before starting (re-emits prior findings).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON {kind, line} per event instead of plain text.",
    )
    p.add_argument(
        "--stale-after",
        type=int,
        default=DEFAULT_STALE_AFTER_SECONDS,
        help=(
            f"Emit STALE_SILENCE if no meaningful event fires for this many "
            f"seconds (default: {DEFAULT_STALE_AFTER_SECONDS}; 0 = disable). "
            f"Operator first principle: silence ≥15 min after PR open = "
            f"something wrong, check PR directly."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(
            args.pr_number,
            repo=args.repo,
            poll_seconds=max(10, args.poll),
            timeout_seconds=args.timeout,
            once=args.once,
            reset_state=args.reset_state,
            as_json=args.json,
            stale_after_seconds=max(0, args.stale_after),
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
