#!/usr/bin/env python3
# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: feedback_monitor_emit_only_terminal_review_and_check_events.md
#                  feedback_pr_auto_review_one_shot_per_open.md
#                  feedback_pr_bot_comments_are_bug_reports.md
"""Canonical PR Monitor for use with the Monitor tool.

Emission contract — agents scan Monitor stdout for these line prefixes:
    REVIEW-INLINE: <author> <path:line> <body[:140]>   first sighting of a
        non-self inline review comment.
    REVIEW-SUMMARY: <author> <state> <body[:140]>      first sighting of a
        non-self review submission (Copilot summary, Codex summary, human).
    CHECK-COMPLETE: <name>: <bucket>                    first time a CI check
        transitions to a TERMINAL bucket (pass/fail/cancel/skipping).
    PR-CLOSED: <state>                                  PR is no longer OPEN;
        the monitor exits.
    ERROR: <reason>                                     fatal startup failure;
        the monitor exits.

Deliberately NOT emitted (filtered as noise that breaks wait discipline, per
feedback_monitor_emit_only_terminal_review_and_check_events.md):
    - Baseline / startup CI states, comments, reviews (recorded silently on
      this process's first successful poll). Re-arm semantics: a fresh process
      treats whatever already exists as baseline; only items arriving AFTER
      first-poll fire events. Trade-off: re-arming a Monitor 10 min after the
      auto-reviewer landed will miss those comments on first poll, but the
      next poll catches anything truly new — and avoids the comment-flood that
      a non-suppressed re-arm would cause.
    - Intermediate CI states (queued, in_progress, pending, mixed).
    - Re-emission of an already-seen terminal state.
    - Self-author comments and review submissions.
    - CI re-runs caused by a push whose head commit was authored by $ME.
      Snapshot-then-clear: the prior terminal state is captured before
      clearing; the same terminal state arriving in the post-push CI re-run
      is silently re-recorded (no CHECK-COMPLETE echo). Only a CHANGED
      terminal state (e.g., pass → fail) emits on the post-push round.

Use:
    Monitor(persistent=true, command="python scripts/pr_monitor.py <PR>")

Args:
    pr            PR number to watch.
    --repo        owner/repo string (auto-detected from `gh repo view`).
    --me          self login (auto-detected from `gh api user`).
    --poll        seconds between polls (default 30).
    --once        single poll then exit; for tests and ad-hoc spot-checks.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any

TERMINAL_BUCKETS = frozenset({"pass", "fail", "cancel", "skipping"})
DEFAULT_POLL_SECONDS = 30
GH_TIMEOUT_SECONDS = 20
COMMENT_BODY_PREVIEW = 140
MAX_CONSECUTIVE_STATE_FAILURES = 3  # after this many consecutive _fetch_pr_state
                                    # failures, emit ERROR and exit. Prevents
                                    # nonexistent-PR / lost-auth silent spin-loops.


def _gh(*args: str) -> tuple[int, str]:
    """Run a gh subcommand. Return (exit_code, stripped_stdout).

    Stderr is dropped intentionally — Monitor stdout is the only event stream
    the agent reads, and gh's warning chatter (rate-limit hints, etc.) is not
    actionable. fail-open: any subprocess failure returns ("", non-zero rc)
    and the caller treats it as "no data this poll" rather than an event.
    """
    try:
        r = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
        return r.returncode, r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 1, ""


def _fetch_me() -> str:
    rc, out = _gh("api", "user", "--jq", ".login")
    return out if rc == 0 else ""


def _fetch_repo() -> str:
    rc, out = _gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    return out if rc == 0 else ""


def _fetch_pr_state(pr: int) -> tuple[str | None, str | None]:
    """Return (state, head_sha). On error: (None, None).

    Guards against gh returning JSON `null` (rare but observed on transient
    upstream failures) — `isinstance(data, dict)` check prevents the
    `None.get(...)` AttributeError that would otherwise crash the Monitor.
    """
    rc, out = _gh("pr", "view", str(pr), "--json", "state,headRefOid")
    if rc != 0 or not out:
        return None, None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    return data.get("state"), data.get("headRefOid")


def _fetch_last_commit_author(pr: int) -> str:
    rc, out = _gh(
        "pr", "view", str(pr), "--json", "commits",
        "--jq", '.commits[-1].authors[0].login // ""',
    )
    return out if rc == 0 else ""


def _fetch_checks(pr: int) -> list[dict[str, Any]]:
    rc, out = _gh("pr", "checks", str(pr), "--json", "name,bucket")
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _fetch_inline_comments(pr: int, repo: str) -> list[dict[str, Any]]:
    # --paginate so PRs with >30 comments don't silently truncate older ones.
    rc, out = _gh("api", "--paginate", f"repos/{repo}/pulls/{pr}/comments")
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _fetch_reviews(pr: int, repo: str) -> list[dict[str, Any]]:
    rc, out = _gh("api", "--paginate", f"repos/{repo}/pulls/{pr}/reviews")
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _emit(line: str) -> None:
    """Single emit point — every CHECK-COMPLETE / REVIEW-* / PR-CLOSED / ERROR
    line goes through here so the Monitor tool sees flushed stdout immediately.

    BrokenPipeError is silenced: if the Monitor tool dies mid-poll, we don't
    want a traceback masking the actual lifecycle event the next poll round
    will surface.
    """
    try:
        print(line, flush=True)
    except BrokenPipeError:
        pass


def _format_inline(c: dict[str, Any]) -> str:
    login = ((c.get("user") or {}).get("login")) or "?"
    path = c.get("path", "") or ""
    line = c.get("line") or c.get("original_line") or ""
    body = (c.get("body") or "")[:COMMENT_BODY_PREVIEW].replace("\n", " ")
    return f"REVIEW-INLINE: {login} {path}:{line} {body}"


def _format_review(r: dict[str, Any]) -> str:
    login = ((r.get("user") or {}).get("login")) or "?"
    state = r.get("state", "") or ""
    body = (r.get("body") or "")[:COMMENT_BODY_PREVIEW].replace("\n", " ")
    return f"REVIEW-SUMMARY: {login} {state} {body}"


def run_once(
    pr: int,
    repo: str,
    me: str,
    seen_terminal: dict[str, str],
    seen_comments: set[Any],
    seen_reviews: set[Any],
    last_head: str | None,
    first_poll: bool,
) -> tuple[str | None, str | None]:
    """One poll round. Returns (pr_state, head_sha). Mutates seen_* sets.

    Returns (None, None) if pr_state could not be read this round; the
    caller treats that as "skip this round" rather than a terminal state.
    """
    state, head = _fetch_pr_state(pr)
    if state and state != "OPEN":
        _emit(f"PR-CLOSED: {state}")
        return state, head
    if state is None:
        return None, None

    # Self-push CI suppression: when head SHA changes AND the new commit is
    # authored by $ME, the CI inevitably re-runs. Snapshot the prior terminal
    # state BEFORE clearing, then suppress emission for any check whose
    # (name, bucket) matches the snapshot. Only a CHANGED terminal state
    # (e.g., pass → fail in the re-run) emits a new CHECK-COMPLETE.
    # Per feedback_monitor_emit_only_terminal_review_and_check_events.md.
    pre_self_push_snapshot: dict[str, str] = {}
    if last_head is not None and head and head != last_head:
        last_author = _fetch_last_commit_author(pr)
        if last_author == me:
            pre_self_push_snapshot = dict(seen_terminal)
            seen_terminal.clear()

    for chk in _fetch_checks(pr):
        name = chk.get("name", "") or ""
        bucket = chk.get("bucket", "") or ""
        if not name or bucket not in TERMINAL_BUCKETS:
            continue
        if seen_terminal.get(name) == bucket:
            continue
        # Suppress emission when: (a) first poll baseline, or (b) this is a
        # self-push round and the pre-clear snapshot already had the same
        # (name, bucket) — i.e., the CI re-run produced identical terminal
        # state. Both cases record silently to keep future polls deduped.
        suppress = first_poll or pre_self_push_snapshot.get(name) == bucket
        if not suppress:
            _emit(f"CHECK-COMPLETE: {name}: {bucket}")
        seen_terminal[name] = bucket

    for c in _fetch_inline_comments(pr, repo):
        cid = c.get("id")
        login = ((c.get("user") or {}).get("login")) or ""
        if cid is None or login == me or cid in seen_comments:
            continue
        seen_comments.add(cid)
        # First-poll baseline-suppress: pre-existing comments on Monitor start
        # are recorded silently so re-arm doesn't flood stale comments.
        if not first_poll:
            _emit(_format_inline(c))

    for r in _fetch_reviews(pr, repo):
        rid = r.get("id")
        login = ((r.get("user") or {}).get("login")) or ""
        if rid is None or login == me or rid in seen_reviews:
            continue
        seen_reviews.add(rid)
        if not first_poll:
            _emit(_format_review(r))

    return state, head


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", type=int, help="PR number to monitor")
    parser.add_argument("--repo", default="", help="owner/repo (auto-detect)")
    parser.add_argument("--me", default="", help="self login (auto-detect)")
    parser.add_argument("--poll", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--once", action="store_true",
                        help="single poll then exit (for tests / spot-checks)")
    args = parser.parse_args(argv)

    me = args.me or _fetch_me()
    repo = args.repo or _fetch_repo()
    if not me or not repo:
        _emit(f"ERROR: failed to resolve me={me!r} or repo={repo!r}")
        return 2

    seen_terminal: dict[str, str] = {}
    seen_comments: set[Any] = set()
    seen_reviews: set[Any] = set()
    last_head: str | None = None
    first_poll = True
    consecutive_state_failures = 0

    while True:
        state, head = run_once(
            args.pr, repo, me,
            seen_terminal, seen_comments, seen_reviews,
            last_head, first_poll,
        )
        if state and state != "OPEN":
            return 0
        if state is None:
            # Transient failure — first_poll NOT advanced; baseline is
            # preserved for the next successful poll. After
            # MAX_CONSECUTIVE_STATE_FAILURES rounds of failure, bail with
            # ERROR rather than silently spin on a nonexistent PR or
            # lost-auth condition.
            consecutive_state_failures += 1
            if consecutive_state_failures >= MAX_CONSECUTIVE_STATE_FAILURES:
                _emit(
                    f"ERROR: cannot read PR state for #{args.pr} after "
                    f"{consecutive_state_failures} attempts (gh missing, PR "
                    f"nonexistent, or auth degraded)"
                )
                return 3
        else:
            consecutive_state_failures = 0
            first_poll = False
        if head:
            last_head = head
        if args.once:
            return 0
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
