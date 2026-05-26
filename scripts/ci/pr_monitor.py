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


def load_state(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {"reported_threads": set(), "reported_failures": set()}
    try:
        with path.open() as f:
            raw = json.load(f)
        return {
            "reported_threads": set(raw.get("reported_threads", [])),
            "reported_failures": set(raw.get("reported_failures", [])),
        }
    except (json.JSONDecodeError, OSError):
        return {"reported_threads": set(), "reported_failures": set()}


def save_state(path: Path, state: dict[str, set[str]]) -> None:
    serialized = {
        "reported_threads": sorted(state["reported_threads"]),
        "reported_failures": sorted(state["reported_failures"]),
    }
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(serialized, f, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# gh CLI wrapper
# ---------------------------------------------------------------------------


def gh_pr_view(pr: int, *, repo: str | None = None) -> dict[str, Any] | None:
    """
    Returns parsed JSON from `gh pr view <pr> --json ...` or None on failure.
    Caller decides whether to retry.
    """
    cmd = [
        "gh",
        "pr",
        "view",
        str(pr),
        "--json",
        "reviewThreads,statusCheckRollup,state",
    ]
    if repo:
        cmd.extend(["--repo", repo])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


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
    state: dict[str, set[str]],
    as_json: bool,
) -> str | None:
    """
    Pull current PR state, emit any new events, update dedup state in place.
    Returns the PR terminal state (MERGED/CLOSED) if reached, else None.
    """
    pr_data = gh_pr_view(pr, repo=repo)
    if pr_data is None:
        return None

    # 1. Findings — only emit if thread id not previously reported
    for finding in extract_unresolved_findings(pr_data):
        tid = finding["tid"]
        if not tid or tid in state["reported_threads"]:
            continue
        emit(format_finding_line(pr, finding), as_json=as_json, kind="finding")
        state["reported_threads"].add(tid)

    # 2. CI failures — dedup by name:conclusion (re-emits if check re-runs and fails again differently)
    for failure in extract_ci_failures(pr_data):
        key = f"{failure['name']}:{failure['conclusion']}"
        if key in state["reported_failures"]:
            continue
        emit(format_ci_fail_line(pr, failure), as_json=as_json, kind="ci_fail")
        state["reported_failures"].add(key)

    # 3. Terminal
    term = is_terminal(pr_data)
    if term:
        emit(format_terminal_line(pr, term), as_json=as_json, kind="terminal")
        return term

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
) -> int:
    repo_key = repo or _gh_default_repo() or "default"
    spath = state_path(repo_key, pr)
    if reset_state and spath.exists():
        spath.unlink()
    state = load_state(spath)

    if once:
        term = tick_once(pr, repo=repo, state=state, as_json=as_json)
        save_state(spath, state)
        return 0

    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    while True:
        term = tick_once(pr, repo=repo, state=state, as_json=as_json)
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
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
