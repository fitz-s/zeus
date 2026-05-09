# Created: 2026-05-09
# Last reused or audited: 2026-05-09
# Authority basis: operator directive 2026-05-09 — pr_thread_reply_waste ADVISORY hook
"""Tests for the pr_thread_reply_waste ADVISORY hook (Principle 2 backstop).

Principle 2: bot comments are bug reports; the fix-commit IS the response.
Agents must not post reply text on bot review threads. This hook fires as
ADVISORY when a command matches a known reply-posting surface.

Test coverage:
  Detection patterns (must match):
    D1. gh pr comment <N>
    D2. GraphQL addPullRequestReviewThreadReply
    D3. GraphQL addPullRequestReviewComment (older form)
    D4. REST pulls/.../comments with POST flag
    D5. REST issues/.../comments with POST flag

  Allow-pass exceptions (must NOT match):
    A1. REST pulls/.../comments GET (list, no POST flag)
    A2. GraphQL resolveReviewThread (correct usage)
    A3. gh pr review (formal review submission)

  Message content:
    M1. Contains "Principle 2"
    M2. Contains "agent_pr_discipline_2026_05_09.md"
    M3. Contains "fix-commit IS the response"
    M4. Contains "resolveReviewThread" (correct-usage example)
    M5. Contains DEFER one-liner exception hint ("Tracked in #")
    M6. Contains "ZEUS_PR_REPLY_ALLOW=1"

  Bypass:
    B1. ZEUS_PR_REPLY_ALLOW=1 returns advisory string (not None, not blocking)
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_DIR = REPO_ROOT / ".claude" / "hooks"


# ---------------------------------------------------------------------------
# Import dispatch module
# ---------------------------------------------------------------------------

def _import_dispatch():
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        spec = importlib.util.spec_from_file_location(
            "dispatch_hook_test",
            str(HOOK_DIR / "dispatch.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    finally:
        sys.stderr = old


_dispatch = _import_dispatch()
_check = _dispatch._run_advisory_check_pr_thread_reply_waste


# ---------------------------------------------------------------------------
# Payload factory
# ---------------------------------------------------------------------------

def _payload(command: str) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


# ---------------------------------------------------------------------------
# D1: gh pr comment <N>
# ---------------------------------------------------------------------------

class TestD1GhPrComment:
    def test_gh_pr_comment_matches(self):
        """gh pr comment <N> -> advisory (top-level PR comment surface)."""
        result = _check(_payload("gh pr comment 42 --body 'looks good'"))
        assert result is not None, "gh pr comment must trigger advisory"
        assert result != _dispatch._BLOCK_SENTINEL

    def test_gh_pr_comment_with_env_prefix_matches(self):
        """VAR=val gh pr comment <N> -> advisory."""
        result = _check(_payload("ZEUS_PR_REPLY_ALLOW=0 gh pr comment 107 -b 'Thanks'"))
        assert result is not None

    def test_unrelated_gh_pr_command_skips(self):
        """gh pr merge 42 -> not a reply, must not trigger."""
        result = _check(_payload("gh pr merge 42 --merge"))
        assert result is None


# ---------------------------------------------------------------------------
# D2: GraphQL addPullRequestReviewThreadReply
# ---------------------------------------------------------------------------

class TestD2GraphQLThreadReply:
    def test_addPullRequestReviewThreadReply_matches(self):
        """GraphQL addPullRequestReviewThreadReply mutation -> advisory."""
        cmd = (
            "gh api graphql -f query='mutation { "
            "addPullRequestReviewThreadReply(input:{threadId:\"T_1\",body:\"noted\"}) "
            "{ comment { id } } }'"
        )
        result = _check(_payload(cmd))
        assert result is not None, "addPullRequestReviewThreadReply must trigger advisory"

    def test_substring_in_heredoc_matches(self):
        """addPullRequestReviewThreadReply in a multi-line command -> matches."""
        cmd = (
            "gh api graphql -f query='\n"
            "mutation {\n"
            "  addPullRequestReviewThreadReply(input: {threadId: \"T_abc\", body: \"ok\"}) {\n"
            "    comment { id }\n"
            "  }\n"
            "}'"
        )
        result = _check(_payload(cmd))
        assert result is not None


# ---------------------------------------------------------------------------
# D3: GraphQL addPullRequestReviewComment (older form)
# ---------------------------------------------------------------------------

class TestD3GraphQLReviewComment:
    def test_addPullRequestReviewComment_matches(self):
        """GraphQL addPullRequestReviewComment -> advisory."""
        cmd = (
            "gh api graphql -f query='mutation { "
            "addPullRequestReviewComment(input:{pullRequestId:\"PR_1\","
            "body:\"see note\",commitOID:\"abc\"}) { comment { id } } }'"
        )
        result = _check(_payload(cmd))
        assert result is not None, "addPullRequestReviewComment must trigger advisory"


# ---------------------------------------------------------------------------
# D4: REST pulls/.../comments with POST flag
# ---------------------------------------------------------------------------

class TestD4RestPullsComments:
    def test_rest_pulls_comments_post_x_flag(self):
        """REST pulls/.../comments -X POST -> advisory."""
        cmd = "gh api repos/owner/zeus/pulls/42/comments -X POST -f body='addressed'"
        result = _check(_payload(cmd))
        assert result is not None, "REST pulls comments POST must trigger advisory"

    def test_rest_pulls_comments_method_post(self):
        """REST pulls/.../comments --method POST -> advisory."""
        cmd = "gh api repos/owner/zeus/pulls/107/comments --method POST -f body='done'"
        result = _check(_payload(cmd))
        assert result is not None

    def test_rest_pulls_comments_body_flag_lowercase_f(self):
        """REST pulls/.../comments -f body=... -> advisory."""
        cmd = "gh api repos/owner/zeus/pulls/42/comments -f body='noted'"
        result = _check(_payload(cmd))
        assert result is not None

    def test_rest_pulls_comments_body_flag_uppercase_f(self):
        """REST pulls/.../comments -F body=... -> advisory (file-field form)."""
        cmd = "gh api repos/owner/zeus/pulls/42/comments -F body='@reply.txt'"
        result = _check(_payload(cmd))
        assert result is not None


# ---------------------------------------------------------------------------
# D5: REST issues/.../comments with POST flag
# ---------------------------------------------------------------------------

class TestD5RestIssuesComments:
    def test_rest_issues_comments_post(self):
        """REST issues/.../comments -X POST -> advisory (PR-as-issue surface)."""
        cmd = "gh api repos/owner/zeus/issues/42/comments -X POST -f body='Tracked in #100'"
        result = _check(_payload(cmd))
        assert result is not None, "REST issues comments POST must trigger advisory"

    def test_rest_issues_comments_method_post(self):
        """REST issues/.../comments --method POST -> advisory."""
        cmd = "gh api repos/owner/zeus/issues/107/comments --method POST -f body='noted'"
        result = _check(_payload(cmd))
        assert result is not None


# ---------------------------------------------------------------------------
# A1: REST pulls/.../comments GET (list) — must NOT match
# ---------------------------------------------------------------------------

class TestA1RestPullsCommentsGet:
    def test_list_pull_comments_no_post_flag_passes(self):
        """REST pulls/.../comments without POST flag -> allow (GET list)."""
        cmd = "gh api repos/owner/zeus/pulls/42/comments"
        result = _check(_payload(cmd))
        assert result is None, f"GET list must not trigger advisory; got {result!r}"

    def test_list_pull_comments_with_jq_passes(self):
        """REST pulls/.../comments --jq ... -> allow (read-only query)."""
        cmd = "gh api repos/owner/zeus/pulls/42/comments --jq '.[].body'"
        result = _check(_payload(cmd))
        assert result is None, f"GET with --jq must not trigger advisory; got {result!r}"


# ---------------------------------------------------------------------------
# A2: resolveReviewThread — must NOT match
# ---------------------------------------------------------------------------

class TestA2ResolveReviewThread:
    def test_resolve_thread_mutation_passes(self):
        """resolveReviewThread GraphQL mutation -> allow (correct usage)."""
        cmd = (
            "gh api graphql -f query='mutation { "
            "resolveReviewThread(input:{threadId:\"T_1\"}) { thread { isResolved } } }'"
        )
        result = _check(_payload(cmd))
        assert result is None, (
            f"resolveReviewThread must not trigger advisory (it IS the correct usage); "
            f"got {result!r}"
        )

    def test_resolve_thread_with_addPullRequestReviewComment_wins_resolve(self):
        """If both resolveReviewThread and addPullRequestReviewComment appear, resolve wins."""
        # Edge case: a command that somehow contains both strings. resolveReviewThread
        # takes precedence (allow-pass override).
        cmd = (
            "gh api graphql -f query='mutation { "
            "resolveReviewThread(input:{threadId:\"T_1\"}) { thread { isResolved } } "
            "# also mentions addPullRequestReviewComment in a comment'"
        )
        result = _check(_payload(cmd))
        assert result is None, "resolveReviewThread must override addPullRequestReviewComment match"


# ---------------------------------------------------------------------------
# A3: gh pr review — must NOT match
# ---------------------------------------------------------------------------

class TestA3GhPrReview:
    def test_gh_pr_review_formal_submission_passes(self):
        """gh pr review --approve -> allow (formal review, not thread reply)."""
        result = _check(_payload("gh pr review 42 --approve"))
        assert result is None, f"gh pr review must not trigger advisory; got {result!r}"

    def test_gh_pr_review_with_body_passes(self):
        """gh pr review --body ... -> allow (formal review submission)."""
        result = _check(_payload("gh pr review 107 --comment --body 'LGTM'"))
        assert result is None, f"gh pr review with body must not trigger advisory; got {result!r}"


# ---------------------------------------------------------------------------
# M1-M6: Educational message content
# ---------------------------------------------------------------------------

def _advisory_message() -> str:
    """Get the advisory message text for a definite-match command."""
    cmd = "gh pr comment 42 --body 'noted'"
    result = _check(_payload(cmd))
    assert result is not None and result != _dispatch._BLOCK_SENTINEL
    return result


class TestMessageContent:
    def test_message_contains_principle_2(self):
        assert "Principle 2" in _advisory_message(), \
            "Advisory message must reference 'Principle 2'"

    def test_message_contains_authority_doc(self):
        assert "agent_pr_discipline_2026_05_09.md" in _advisory_message(), \
            "Advisory message must cite authority doc"

    def test_message_contains_fix_commit_is_response(self):
        assert "fix-commit IS the response" in _advisory_message(), \
            "Advisory message must state 'fix-commit IS the response'"

    def test_message_contains_resolve_mutation_example(self):
        assert "resolveReviewThread" in _advisory_message(), \
            "Advisory message must show resolveReviewThread mutation as correct usage"

    def test_message_contains_defer_exception(self):
        msg = _advisory_message()
        assert "Tracked in #" in msg or "DEFER" in msg, \
            "Advisory message must mention the DEFER one-liner exception"

    def test_message_contains_bypass_env(self):
        assert "ZEUS_PR_REPLY_ALLOW=1" in _advisory_message(), \
            "Advisory message must mention the ZEUS_PR_REPLY_ALLOW=1 bypass"


# ---------------------------------------------------------------------------
# B1: Bypass env
# ---------------------------------------------------------------------------

class TestBypassEnv:
    def test_bypass_returns_advisory_not_none(self, monkeypatch):
        """ZEUS_PR_REPLY_ALLOW=1 must return a non-None advisory string (not silent)."""
        monkeypatch.setenv("ZEUS_PR_REPLY_ALLOW", "1")
        result = _check(_payload("gh pr comment 42 --body 'Tracked in #100'"))
        assert result is not None, "bypass must still emit advisory (not silence)"
        assert result != _dispatch._BLOCK_SENTINEL, "bypass must not block"

    def test_bypass_message_mentions_principle_2(self, monkeypatch):
        """Bypass advisory message must still anchor on Principle 2."""
        monkeypatch.setenv("ZEUS_PR_REPLY_ALLOW", "1")
        result = _check(_payload("gh pr comment 42 --body 'Tracked in #100'"))
        assert "Principle 2" in result, \
            f"Bypass message must reference Principle 2; got: {result!r}"

    def test_no_bypass_on_non_matching_command(self, monkeypatch):
        """ZEUS_PR_REPLY_ALLOW=1 on a non-matching command -> still None."""
        monkeypatch.setenv("ZEUS_PR_REPLY_ALLOW", "1")
        result = _check(_payload("gh pr merge 42 --merge"))
        assert result is None
