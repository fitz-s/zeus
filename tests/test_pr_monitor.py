# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Purpose: Antibody tests pinning the 7 filter contracts of scripts/pr_monitor.py
#   so a future refactor that regresses a filter fails the build.
# Reuse: Inspect scripts/pr_monitor.py docstring (emission contract + filters)
#   before adding/changing tests.
# Authority basis: feedback_antibody_recursion_metaverify_essential.md
#                  feedback_monitor_emit_only_terminal_review_and_check_events.md
"""Antibody tests for scripts/pr_monitor.py — the canonical PR Monitor.

These tests inject controlled fake gh outputs (no subprocess) so the filter
contracts are pinned in source:
    1. First-poll baseline (CI, comments, reviews) recorded silently.
    2. Non-self comments/reviews are emitted on later polls; self-author are not.
    3. Already-seen terminal CI states are never re-emitted.
    4. PR transitioning out of OPEN emits PR-CLOSED and terminates.
    5. Any push (head SHA change, any author) does NOT reset the terminal
       tracker. The plain dedup on `seen_terminal` survives the push event
       and silently absorbs CI re-runs landing at the SAME terminal bucket
       even across multiple intermediate in_progress polls. CHANGED terminal
       states (pass→fail in re-run) still emit. Anchor: PR #133 live
       reproduction of duplicate CHECK-COMPLETE proved a per-poll snapshot
       cannot bridge the multi-poll window between push and re-run terminal.
    6. Transient _fetch_pr_state failures don't advance first_poll; baseline
       is preserved for the next successful poll. After 3 consecutive
       failures, main emits ERROR and exits non-zero.
    7. gh returning JSON `null` (rare upstream issue) doesn't crash.

Meta-verification (per feedback_antibody_recursion_metaverify_essential):
each test is constructed so that breaking the corresponding filter in
scripts/pr_monitor.py would make the assertion fail. Sed-break/restore
proofs run during PR authoring; the multi-poll push test
(test_push_then_ci_rerun_across_polls_does_not_echo) was added
specifically because the original single-poll snapshot test couldn't
catch the across-polls regression that fired live on PR #133.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pr_monitor  # noqa: E402


def _patch_fetches(monkeypatch, *,
                   state="OPEN", head="sha0",
                   checks=None, comments=None, reviews=None):
    monkeypatch.setattr(pr_monitor, "_fetch_pr_state",
                        lambda pr: (state, head))
    monkeypatch.setattr(pr_monitor, "_fetch_checks",
                        lambda pr: checks or [])
    monkeypatch.setattr(pr_monitor, "_fetch_inline_comments",
                        lambda pr, repo: comments or [])
    monkeypatch.setattr(pr_monitor, "_fetch_reviews",
                        lambda pr, repo: reviews or [])


def _capture_run_once(*args, **kwargs) -> list[str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        pr_monitor.run_once(*args, **kwargs)
    return [ln for ln in buf.getvalue().splitlines() if ln.strip()]


def test_first_poll_records_terminal_ci_but_does_not_emit(monkeypatch):
    """Contract 1: first poll baseline — record but no CHECK-COMPLETE lines."""
    _patch_fetches(
        monkeypatch,
        checks=[{"name": "tests", "bucket": "pass"},
                {"name": "lint", "bucket": "fail"}],
    )
    seen_terminal: dict[str, str] = {}
    lines = _capture_run_once(
        132, "owner/repo", "me",
        seen_terminal, set(), set(),
        last_head=None, first_poll=True,
    )
    assert not any(l.startswith("CHECK-COMPLETE") for l in lines), \
        f"first-poll baseline must not emit CHECK-COMPLETE; got {lines!r}"
    assert seen_terminal == {"tests": "pass", "lint": "fail"}, \
        "baseline states must be recorded so future polls suppress re-emission"


def test_second_poll_emits_only_new_terminal_transitions(monkeypatch):
    """Contract 3: already-seen terminal states are never re-emitted."""
    _patch_fetches(
        monkeypatch,
        checks=[{"name": "tests", "bucket": "pass"},
                {"name": "lint", "bucket": "fail"},
                {"name": "build", "bucket": "pass"}],
    )
    seen_terminal = {"tests": "pass", "lint": "fail"}
    lines = _capture_run_once(
        132, "owner/repo", "me",
        seen_terminal, set(), set(),
        last_head="sha0", first_poll=False,
    )
    assert lines == ["CHECK-COMPLETE: build: pass"], \
        f"only NEW terminal transitions must emit; got {lines!r}"


def test_intermediate_ci_states_are_suppressed(monkeypatch):
    """Contract: intermediate buckets (queued, in_progress, pending) never emit."""
    _patch_fetches(
        monkeypatch,
        checks=[{"name": "tests", "bucket": "pending"},
                {"name": "build", "bucket": "in_progress"}],
    )
    lines = _capture_run_once(
        132, "owner/repo", "me", {}, set(), set(),
        last_head="sha0", first_poll=False,
    )
    assert lines == [], f"intermediate states must be silent; got {lines!r}"


def test_self_comments_and_reviews_are_filtered(monkeypatch):
    """Contract 2: self-author comments and reviews must NOT emit."""
    _patch_fetches(
        monkeypatch,
        comments=[
            {"id": 1, "user": {"login": "me"}, "body": "my own comment",
             "path": "x.py", "line": 1},
            {"id": 2, "user": {"login": "Copilot"}, "body": "bot comment",
             "path": "y.py", "line": 5},
        ],
        reviews=[
            {"id": 10, "user": {"login": "me"}, "state": "APPROVED", "body": "ok"},
            {"id": 11, "user": {"login": "codex"}, "state": "COMMENTED",
             "body": "findings here"},
        ],
    )
    lines = _capture_run_once(
        132, "owner/repo", "me", {}, set(), set(),
        last_head="sha0", first_poll=False,
    )
    inline = [l for l in lines if l.startswith("REVIEW-INLINE")]
    summary = [l for l in lines if l.startswith("REVIEW-SUMMARY")]
    assert len(inline) == 1 and "Copilot" in inline[0], \
        f"self-author inline must be filtered; got {inline!r}"
    assert len(summary) == 1 and "codex" in summary[0], \
        f"self-author review must be filtered; got {summary!r}"


def test_already_seen_comments_and_reviews_not_re_emitted(monkeypatch):
    """Contract: comments/reviews already in seen_* sets do not re-emit."""
    _patch_fetches(
        monkeypatch,
        comments=[{"id": 7, "user": {"login": "Copilot"}, "body": "first",
                   "path": "z.py", "line": 2}],
        reviews=[{"id": 99, "user": {"login": "codex"}, "state": "COMMENTED",
                  "body": "first"}],
    )
    lines = _capture_run_once(
        132, "owner/repo", "me", {}, {7}, {99},
        last_head="sha0", first_poll=False,
    )
    assert lines == [], f"already-seen items must not re-emit; got {lines!r}"


def test_push_with_same_terminal_state_does_not_echo(monkeypatch):
    """Contract 5: a head SHA change with identical terminal CI state in the
    re-run does NOT emit CHECK-COMPLETE. The plain dedup on `seen_terminal`
    handles this without any snapshot/clear mechanism."""
    _patch_fetches(
        monkeypatch,
        head="sha1",
        checks=[{"name": "tests", "bucket": "pass"}],
    )
    seen_terminal = {"tests": "pass"}
    lines = _capture_run_once(
        132, "owner/repo", "me",
        seen_terminal, set(), set(),
        last_head="sha0", first_poll=False,
    )
    assert not any(l.startswith("CHECK-COMPLETE") for l in lines), \
        f"push round must not echo unchanged terminal state; got {lines!r}"
    assert seen_terminal == {"tests": "pass"}


def test_push_with_changed_terminal_state_does_emit(monkeypatch):
    """Contract 5 positive: if the CI re-run's terminal state DIFFERS from
    the prior known state (pass → fail), that IS a new event and emits."""
    _patch_fetches(
        monkeypatch,
        head="sha1",
        checks=[{"name": "tests", "bucket": "fail"}],  # was pass before push
    )
    seen_terminal = {"tests": "pass"}
    lines = _capture_run_once(
        132, "owner/repo", "me",
        seen_terminal, set(), set(),
        last_head="sha0", first_poll=False,
    )
    assert any("CHECK-COMPLETE: tests: fail" in l for l in lines), \
        f"changed terminal state after push must emit; got {lines!r}"
    assert seen_terminal == {"tests": "fail"}


def test_push_then_ci_rerun_across_polls_does_not_echo(monkeypatch):
    """Contract 5 multi-poll: the anchor scenario from PR #133 v1 bug.
    Push happens between poll 1 (CI was pass) and poll 2 (CI now pending).
    Poll 3 sees CI terminal again at the same bucket. The intermediate
    in_progress poll MUST NOT cause the eventual terminal re-arrival to
    look NEW. seen_terminal entries must persist across the entire window.
    """
    seen_terminal: dict[str, str] = {"tests": "pass"}

    # Poll 2: head changed to sha1, CI in_progress (intermediate, not terminal)
    _patch_fetches(
        monkeypatch,
        head="sha1",
        checks=[{"name": "tests", "bucket": "in_progress"}],
    )
    p2 = _capture_run_once(
        132, "owner/repo", "me",
        seen_terminal, set(), set(),
        last_head="sha0", first_poll=False,
    )
    assert p2 == [], f"intermediate state must not emit; got {p2!r}"
    assert seen_terminal == {"tests": "pass"}, \
        "seen_terminal must survive across the push event"

    # Poll 3: still on sha1, CI re-run finished at same terminal state
    _patch_fetches(
        monkeypatch,
        head="sha1",
        checks=[{"name": "tests", "bucket": "pass"}],
    )
    p3 = _capture_run_once(
        132, "owner/repo", "me",
        seen_terminal, set(), set(),
        last_head="sha1", first_poll=False,
    )
    assert p3 == [], \
        f"re-arrival at same terminal must NOT echo across polls; got {p3!r}"
    assert seen_terminal == {"tests": "pass"}


def test_push_with_brand_new_check_emits(monkeypatch):
    """A push (any author) that introduces a brand-new check (not in
    seen_terminal) reaching terminal state emits CHECK-COMPLETE. The
    plain dedup naturally handles this — no special-case branch needed."""
    _patch_fetches(
        monkeypatch,
        head="sha1",
        checks=[{"name": "tests", "bucket": "pass"},
                {"name": "build", "bucket": "pass"}],  # brand new check
    )
    seen_terminal = {"tests": "pass"}
    lines = _capture_run_once(
        132, "owner/repo", "me",
        seen_terminal, set(), set(),
        last_head="sha0", first_poll=False,
    )
    assert "build" in seen_terminal, \
        "brand-new check must be tracked after first terminal sighting"
    assert any("CHECK-COMPLETE: build" in l for l in lines), \
        f"brand-new terminal check must emit; got {lines!r}"
    assert not any("CHECK-COMPLETE: tests" in l for l in lines), \
        f"pre-known check at same bucket must not echo; got {lines!r}"


def test_pr_closed_emits_and_terminates(monkeypatch):
    """Contract 4: PR not OPEN → PR-CLOSED line, run_once returns state."""
    _patch_fetches(monkeypatch, state="MERGED", head="shaX")
    buf = io.StringIO()
    with redirect_stdout(buf):
        state, head = pr_monitor.run_once(
            132, "owner/repo", "me",
            {}, set(), set(),
            last_head="sha0", first_poll=False,
        )
    assert state == "MERGED"
    out = buf.getvalue()
    assert "PR-CLOSED: MERGED" in out, f"missing PR-CLOSED line; got {out!r}"


def test_pr_state_unavailable_returns_skip_sentinel(monkeypatch):
    """When gh fails to return pr state, run_once returns (None, None) so the
    caller treats the round as 'skip', not as a terminal close."""
    _patch_fetches(monkeypatch, state=None, head=None)
    state, head = pr_monitor.run_once(
        132, "owner/repo", "me",
        {}, set(), set(),
        last_head=None, first_poll=False,
    )
    assert state is None and head is None


def test_main_errors_when_repo_or_me_cannot_be_resolved(monkeypatch):
    """Without an authenticated gh CLI, main returns 2 and emits ERROR."""
    monkeypatch.setattr(pr_monitor, "_fetch_me", lambda: "")
    monkeypatch.setattr(pr_monitor, "_fetch_repo", lambda: "")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = pr_monitor.main(["132", "--once"])
    assert rc == 2
    assert "ERROR" in buf.getvalue()


def test_main_once_returns_zero_when_pr_open(monkeypatch):
    """--once: one poll on an OPEN PR returns 0 cleanly."""
    monkeypatch.setattr(pr_monitor, "_fetch_me", lambda: "me")
    monkeypatch.setattr(pr_monitor, "_fetch_repo", lambda: "owner/repo")
    _patch_fetches(monkeypatch, state="OPEN", head="sha0")
    rc = pr_monitor.main(["132", "--once"])
    assert rc == 0


def test_first_poll_baselines_pre_existing_non_self_comments(monkeypatch):
    """Contract 1 extended: first poll baselines comments+reviews too. The
    re-arm anchor (Monitor restart on a PR with N historical comments)
    must not flood. Pre-existing non-self items are recorded silently;
    only items arriving AFTER first poll fire events."""
    seen_comments: set = set()
    seen_reviews: set = set()
    _patch_fetches(
        monkeypatch,
        comments=[{"id": 99, "user": {"login": "Copilot"}, "body": "stale",
                   "path": "a.py", "line": 1}],
        reviews=[{"id": 50, "user": {"login": "codex"}, "state": "COMMENTED",
                  "body": "stale review"}],
    )
    lines = _capture_run_once(
        132, "owner/repo", "me",
        {}, seen_comments, seen_reviews,
        last_head=None, first_poll=True,
    )
    assert lines == [], \
        f"first-poll baseline must not emit comments/reviews; got {lines!r}"
    assert 99 in seen_comments, "comment id baselined for future dedup"
    assert 50 in seen_reviews, "review id baselined for future dedup"


def test_second_poll_emits_new_comment_after_baseline(monkeypatch):
    """Contract 2 + 1: after baseline silences pre-existing, a comment
    arriving on a later poll DOES emit."""
    # Round 1: baseline a pre-existing comment.
    _patch_fetches(
        monkeypatch,
        comments=[{"id": 1, "user": {"login": "Copilot"}, "body": "old",
                   "path": "a.py", "line": 1}],
    )
    seen_comments: set = set()
    r1 = _capture_run_once(
        132, "owner/repo", "me",
        {}, seen_comments, set(),
        last_head=None, first_poll=True,
    )
    assert r1 == []
    # Round 2: a NEW comment shows up alongside the baselined one.
    _patch_fetches(
        monkeypatch,
        comments=[
            {"id": 1, "user": {"login": "Copilot"}, "body": "old",
             "path": "a.py", "line": 1},
            {"id": 2, "user": {"login": "codex"}, "body": "new finding",
             "path": "b.py", "line": 7},
        ],
    )
    r2 = _capture_run_once(
        132, "owner/repo", "me",
        {}, seen_comments, set(),
        last_head="sha0", first_poll=False,
    )
    assert len(r2) == 1 and "codex" in r2[0] and "b.py:7" in r2[0], \
        f"second-poll new comment must emit exactly once; got {r2!r}"


def test_main_exits_with_error_after_consecutive_state_failures(monkeypatch):
    """Contract 6: nonexistent PR / lost auth → ERROR emit + non-zero exit
    within MAX_CONSECUTIVE_STATE_FAILURES polls, not silent spin."""
    monkeypatch.setattr(pr_monitor, "_fetch_me", lambda: "me")
    monkeypatch.setattr(pr_monitor, "_fetch_repo", lambda: "owner/repo")
    # Every poll returns (None, None) — gh "cannot resolve PR"-class failure.
    monkeypatch.setattr(pr_monitor, "_fetch_pr_state", lambda pr: (None, None))
    monkeypatch.setattr(pr_monitor, "_fetch_checks", lambda pr: [])
    monkeypatch.setattr(pr_monitor, "_fetch_inline_comments", lambda pr, r: [])
    monkeypatch.setattr(pr_monitor, "_fetch_reviews", lambda pr, r: [])
    monkeypatch.setattr(pr_monitor.time, "sleep", lambda s: None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        # --poll 1 (smallest valid) + monkeypatched sleep keeps the test fast.
        rc = pr_monitor.main(["9999", "--poll", "1"])
    assert rc == 3, f"must exit 3 after consecutive failures; got {rc}"
    assert "ERROR" in buf.getvalue() and "9999" in buf.getvalue(), \
        f"must emit ERROR line; got {buf.getvalue()!r}"


def test_transient_first_poll_failure_preserves_baseline(monkeypatch):
    """Contract 6 fail-open: if first poll fails (gh transient), the next
    successful poll still treats itself as baseline — no noise flood."""
    monkeypatch.setattr(pr_monitor, "_fetch_me", lambda: "me")
    monkeypatch.setattr(pr_monitor, "_fetch_repo", lambda: "owner/repo")
    monkeypatch.setattr(pr_monitor.time, "sleep", lambda s: None)
    monkeypatch.setattr(pr_monitor, "_fetch_inline_comments", lambda pr, r: [])
    monkeypatch.setattr(pr_monitor, "_fetch_reviews", lambda pr, r: [])

    poll_seq = iter([
        (None, None),                  # poll 1: transient failure
        ("OPEN", "sha0"),              # poll 2: success — must baseline
    ])
    monkeypatch.setattr(pr_monitor, "_fetch_pr_state",
                        lambda pr: next(poll_seq))
    # Note: round 1 returns early at state-fetch failure and never reaches
    # _fetch_checks, so the iter has just one entry — consumed on round 2.
    checks_seq = iter([
        [{"name": "tests", "bucket": "pass"}],           # poll 2: baseline
    ])
    monkeypatch.setattr(pr_monitor, "_fetch_checks",
                        lambda pr: next(checks_seq))

    # --once would stop after one round. Run twice via repeated main calls is
    # awkward; just call run_once twice manually with shared state.
    seen_terminal: dict[str, str] = {}
    first_poll = True
    # Round 1 — failure.
    s1, _ = pr_monitor.run_once(
        132, "owner/repo", "me",
        seen_terminal, set(), set(),
        last_head=None, first_poll=first_poll,
    )
    assert s1 is None
    # first_poll stays True because state was None (mimics main's gating).
    # Round 2 — success; must baseline silently.
    buf = io.StringIO()
    with redirect_stdout(buf):
        s2, _ = pr_monitor.run_once(
            132, "owner/repo", "me",
            seen_terminal, set(), set(),
            last_head=None, first_poll=first_poll,
        )
    assert s2 == "OPEN"
    assert buf.getvalue() == "", \
        f"recovered first poll must baseline silently; got {buf.getvalue()!r}"
    assert seen_terminal == {"tests": "pass"}


def test_pr_state_handles_json_null_response(monkeypatch):
    """Contract 7: gh returning JSON literal `null` must not crash."""
    def fake_gh(*args):
        if args[0] == "pr" and args[1] == "view":
            return 0, "null"
        return 0, ""
    monkeypatch.setattr(pr_monitor, "_gh", fake_gh)
    state, head = pr_monitor._fetch_pr_state(132)
    assert state is None and head is None


def test_parse_paginated_json_flattens_slurp_wrapper():
    """gh api --paginate --slurp returns [[page1...], [page2...], ...].
    The parser must flatten to a single item list. Anchor: PR #133 Codex
    P2 (silent comment loss past 30 items without --slurp+flatten)."""
    multi_page = json.dumps([
        [{"id": 1, "body": "p1a"}, {"id": 2, "body": "p1b"}],
        [{"id": 3, "body": "p2a"}],
    ])
    flat = pr_monitor._parse_paginated_json(multi_page)
    assert [c["id"] for c in flat] == [1, 2, 3]


def test_parse_paginated_json_handles_single_page_wrapper():
    """Single-page slurp output: [[items...]] — still flattens correctly."""
    single_page = json.dumps([[{"id": 1, "body": "only"}]])
    flat = pr_monitor._parse_paginated_json(single_page)
    assert [c["id"] for c in flat] == [1]


def test_parse_paginated_json_returns_empty_on_garbage():
    """Malformed JSON must NOT crash — fail-open to []."""
    assert pr_monitor._parse_paginated_json("not json") == []
    assert pr_monitor._parse_paginated_json("") == []
    assert pr_monitor._parse_paginated_json(json.dumps({"unexpected": "shape"})) == []


def test_main_rejects_nonpositive_poll(monkeypatch):
    """--poll 0 or negative must error early, not spin-loop and burn gh."""
    monkeypatch.setattr(pr_monitor, "_fetch_me", lambda: "me")
    monkeypatch.setattr(pr_monitor, "_fetch_repo", lambda: "owner/repo")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = pr_monitor.main(["132", "--poll", "0"])
    assert rc == 4
    assert "ERROR" in buf.getvalue() and "--poll" in buf.getvalue()


def test_fetch_inline_comments_uses_slurp_flag(monkeypatch):
    """Wire test: _fetch_inline_comments must pass --paginate --slurp to gh.
    Without --slurp, multi-page PRs silently drop comments past page 1."""
    captured_args: list[tuple] = []
    def fake_gh(*args):
        captured_args.append(args)
        return 0, json.dumps([[{"id": 1, "user": {"login": "x"}}]])
    monkeypatch.setattr(pr_monitor, "_gh", fake_gh)
    pr_monitor._fetch_inline_comments(132, "owner/repo")
    assert any("--paginate" in a and "--slurp" in a
               for a in captured_args), \
        f"both --paginate and --slurp must be in gh args; got {captured_args!r}"
