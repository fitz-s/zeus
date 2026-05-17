#!/usr/bin/env python3
# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Purpose: Canonical PR Monitor script — single source of filter logic for
#   the Monitor tool armed after `gh pr create` / `gh pr ready` succeeds.
# Reuse: Inspect `.claude/hooks/dispatch.py::_run_advisory_check_pr_open_monitor_arm`
#   and `tests/test_pr_monitor.py` antibodies before adjusting filter contracts.
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
    - CI re-runs caused by ANY push (self or other) — the per-check terminal
      tracker survives push events, so a re-run that produces the same
      (name, bucket) silently dedups. Only a CHANGED terminal state
      (e.g., pass → fail in the re-run) emits a new CHECK-COMPLETE. This is
      the entire mechanism: there is no special-case self-push branch,
      because the original "snapshot-then-clear" attempt failed across the
      common multi-poll gap between push detection and re-run terminal
      arrival (PR #133 live anchor 2026-05-17).

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
    actionable. fail-open: on TimeoutExpired/FileNotFoundError/OSError, returns
    (1, "") so the caller treats it as "no data this poll" rather than an
    event. Tuple ordering is (rc, stdout) — never (stdout, rc).
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


def _fetch_checks(pr: int) -> list[dict[str, Any]]:
    rc, out = _gh("pr", "checks", str(pr), "--json", "name,bucket")
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _parse_paginated_json(out: str) -> list[dict[str, Any]]:
    """Parse `gh api --paginate --slurp` output.

    With --slurp, gh wraps each page as an element of the outer array, so the
    output is `[[page1...], [page2...], ...]` — flatten to a single list of
    items. Tolerates the single-page case (one wrapper array containing one
    page array). Returns [] on parse failure or unexpected shape.

    Without --slurp, multi-page --paginate emits concatenated JSON arrays
    that `json.loads` cannot parse — silently dropping ALL comments past
    page 1. The original anchor for adding --slurp here is PR #133 review
    by chatgpt-codex-connector[bot].
    """
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    # If --slurp wrapper present: data is a list of lists. Flatten.
    # If somehow a flat list slips through (defensive), pass through.
    flat: list[dict[str, Any]] = []
    for entry in data:
        if isinstance(entry, list):
            flat.extend(item for item in entry if isinstance(item, dict))
        elif isinstance(entry, dict):
            flat.append(entry)
    return flat


def _fetch_inline_comments(pr: int, repo: str) -> list[dict[str, Any]]:
    # --paginate + --slurp: traverses every page AND wraps the pages so the
    # output is parseable as a single JSON array (concatenated arrays without
    # --slurp would silently drop pages 2+ to JSONDecodeError → []).
    rc, out = _gh(
        "api", "--paginate", "--slurp",
        f"repos/{repo}/pulls/{pr}/comments",
    )
    if rc != 0 or not out:
        return []
    return _parse_paginated_json(out)


def _fetch_reviews(pr: int, repo: str) -> list[dict[str, Any]]:
    rc, out = _gh(
        "api", "--paginate", "--slurp",
        f"repos/{repo}/pulls/{pr}/reviews",
    )
    if rc != 0 or not out:
        return []
    return _parse_paginated_json(out)


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

    # CI dedup: the per-check terminal tracker (`seen_terminal`) survives
    # across polls AND across push events. A check that re-runs to the same
    # terminal state hits `seen_terminal.get(name) == bucket` and continues
    # silently. A check that changes terminal state (pass → fail) falls into
    # the body and emits — exactly what we want.
    #
    # No special-case self-push branch: the previous "snapshot-then-clear"
    # design (PR #133 v1) failed because CI re-runs typically settle several
    # polls AFTER the push event, and the local snapshot couldn't survive
    # that gap. Removing the clear is simpler, correct across any number of
    # intermediate polls, and handles foreign pushes identically. Anchor:
    # PR #133 live emission of duplicate CHECK-COMPLETE after c81499c36e.
    for chk in _fetch_checks(pr):
        name = chk.get("name", "") or ""
        bucket = chk.get("bucket", "") or ""
        if not name or bucket not in TERMINAL_BUCKETS:
            continue
        if seen_terminal.get(name) == bucket:
            continue
        # First-poll baseline: record but never emit.
        if not first_poll:
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

    # --poll <= 0 would spin-loop and burn gh rate limits. Reject early. Tests
    # that need a fast cadence should monkeypatch time.sleep instead.
    if args.poll <= 0:
        _emit(f"ERROR: --poll must be >= 1 (got {args.poll})")
        return 4

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
