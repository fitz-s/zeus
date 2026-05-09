# Created: 2026-05-09
# Last reused or audited: 2026-05-09
# Authority basis: operator directive 2026-05-09 — pr_create_loc_accumulation redesign
#   (300 LOC threshold + educational message + author detection)
"""Tests for the redesigned pr_create_loc_accumulation hook.

The redesign moved from a "commits<2 AND LOC<80" rule to a single
"self-authored LOC < 300" rule, with carry-over commits (no
`Co-Authored-By: Claude` line) excluded from the count. The block
message switched from a 6-line accusation to a multi-paragraph
educational text explaining the auto-reviewer cost economics so the
agent reasons about whether to bundle/bypass rather than rote-bypass.

These tests verify:
- 300 LOC threshold (not 80)
- Author detection excludes carry-over commits
- Educational message contains the cost-reasoning keywords
- Bypass env still works
- Heredoc content with `gh pr create` inside does NOT trigger
- Regression anchor: <300 self-LOC blocks even when total LOC exceeds 300
  (the PR #105 micro-PR scenario that motivated this redesign).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = REPO_ROOT / ".claude" / "hooks"
sys.path.insert(0, str(HOOK_DIR))

# Import lazily so the boot self-test runs; capture handler reference.
import dispatch  # type: ignore  # noqa: E402

_BLOCK = dispatch._BLOCK_SENTINEL
_handler = dispatch._run_advisory_check_pr_create_loc_accumulation
_loc_compute = dispatch._agent_authored_loc_in_range


def _mk_payload(command: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "pr-loc-test",
        "agent_id": "test-agent",
    }


# ---------------------------------------------------------------------------
# Regex anchoring — heredoc body containing "gh pr create" must NOT trigger
# ---------------------------------------------------------------------------


def test_handler_skips_non_gh_pr_command() -> None:
    """Random bash commands pass through (return None)."""
    assert _handler(_mk_payload("ls -la")) is None
    assert _handler(_mk_payload("git status")) is None


def test_handler_skips_heredoc_with_gh_pr_create_inside() -> None:
    """A heredoc whose body contains 'gh pr create' must not trigger.

    The regex is anchored to the command head; a body like
        bash -c "echo 'do not run gh pr create yet'"
    or a heredoc that mentions the phrase must not be misread as the
    actual gh-pr-create call.
    """
    # The full heredoc form starts with cat<<EOF or similar at command head.
    cmd = "cat <<EOF\nReminder: gh pr create when ready\nEOF"
    assert _handler(_mk_payload(cmd)) is None


# ---------------------------------------------------------------------------
# Threshold — 300 LOC, not 80
# ---------------------------------------------------------------------------


def test_threshold_constant_is_300_in_handler_source() -> None:
    """The handler source must reference 300 (not 80) as the LOC threshold.

    Belt-and-braces: a config drift between registry.yaml and the source
    file would silently re-enable the old 80-LOC rule.
    """
    src = (HOOK_DIR / "dispatch.py").read_text()
    # Find the handler region (between 'def _run_advisory_check_pr_create_loc_accumulation'
    # and the next top-level def)
    start = src.index("def _run_advisory_check_pr_create_loc_accumulation")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    assert "LOC_THRESHOLD = 300" in body, (
        f"handler body must declare LOC_THRESHOLD = 300; not found.\n"
        f"body excerpt: {body[:500]}"
    )
    # Old threshold must NOT be present as the live constant
    assert "LOC_THRESHOLD = 80" not in body, (
        "old 80-LOC threshold leaked back into the handler"
    )


# ---------------------------------------------------------------------------
# Educational message contents — must teach, not just deny
# ---------------------------------------------------------------------------


def test_block_message_contains_cost_reasoning(monkeypatch, capsys) -> None:
    """The block message must explain WHY (paid auto-reviewers, tier mismatch).

    Pure-rule denials don't change agent behavior across sessions; the
    reasoning text is the load-bearing part of the redesign.
    """
    # Force the path: bypass off, gh-pr-create command, low self_loc
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (50, 50, 1),
    )
    payload = _mk_payload("gh pr create --title test --body test")
    result = _handler(payload)
    captured = capsys.readouterr()
    msg = captured.err
    assert result == _BLOCK, "low self_loc must block"

    required_phrases = [
        "auto-reviewer",  # cost framing
        "tier",            # tier-mismatch waste framing
        "300",             # threshold cited
        "Decision tree",   # decision tree present
        "ZEUS_PR_ALLOW_TINY",  # bypass mentioned
        "agent_pr_discipline_2026_05_09.md",  # authority doc cited
    ]
    for phrase in required_phrases:
        assert phrase in msg, (
            f"educational message missing required phrase {phrase!r}.\n"
            f"message excerpt:\n{msg[:1500]}"
        )


def test_above_threshold_passes(monkeypatch) -> None:
    """self_loc >= 300 must NOT block — handler returns None."""
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (350, 350, 3),
    )
    payload = _mk_payload("gh pr create --title test")
    assert _handler(payload) is None


def test_at_threshold_passes(monkeypatch) -> None:
    """Exactly 300 self_loc passes (`< 300` is the gate, not `<= 300`)."""
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (300, 300, 2),
    )
    payload = _mk_payload("gh pr create --title test")
    assert _handler(payload) is None


def test_below_threshold_by_one_blocks(monkeypatch) -> None:
    """299 self_loc blocks (boundary anchor)."""
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (299, 299, 2),
    )
    payload = _mk_payload("gh pr create --title test")
    assert _handler(payload) == _BLOCK


# ---------------------------------------------------------------------------
# Bypass — ZEUS_PR_ALLOW_TINY=1 degrades to advisory-only
# ---------------------------------------------------------------------------


def test_bypass_env_returns_advisory_text(monkeypatch) -> None:
    """ZEUS_PR_ALLOW_TINY=1 returns a non-blocking advisory string."""
    monkeypatch.setenv("ZEUS_PR_ALLOW_TINY", "1")
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (50, 50, 1),
    )
    payload = _mk_payload("gh pr create --title test")
    result = _handler(payload)
    assert result is not None and result != _BLOCK
    assert "ADVISORY" in result
    assert "ZEUS_PR_ALLOW_TINY" in result


# ---------------------------------------------------------------------------
# Author detection — carry-over commits don't count toward 300
# ---------------------------------------------------------------------------


def test_carry_over_only_blocks_with_self_loc_zero(monkeypatch) -> None:
    """When all commits in range are carry-over, self_loc=0 → block.

    Regression anchor for the PR #105 scenario: the agent's branch
    contains operator's local-main commits but no agent commits. Total
    LOC may be high (operator's docs), but agent contribution is zero
    and the rule should still block.
    """
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    # Total 200 (all carry-over), self_loc 0
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (200, 0, 1),
    )
    payload = _mk_payload("gh pr create --title test")
    assert _handler(payload) == _BLOCK


def test_mixed_total_high_self_low_blocks(monkeypatch, capsys) -> None:
    """Total 235 LOC (175 carry + 59 agent) → blocks because self_loc=59 < 300.

    This is exactly the PR #105 scenario: my fix was 59 LOC, but the
    branch carried operator's 176-LOC docs commit, inflating total to
    235. Old hook (LOC<80) saw 235 and passed; new hook sees self_loc=59
    and blocks — the redesign closes the loophole.
    """
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (235, 59, 2),
    )
    payload = _mk_payload("gh pr create --title test")
    result = _handler(payload)
    captured = capsys.readouterr()
    assert result == _BLOCK
    assert "self-authored LOC:     59" in captured.err
    assert "total LOC since base:  235" in captured.err


def test_mixed_total_high_self_above_passes(monkeypatch) -> None:
    """Total 500 with self_loc 350 (above 300) → no block.

    Confirms author detection rewards genuine agent work even when
    the branch picked up unrelated operator commits.
    """
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (500, 350, 3),
    )
    payload = _mk_payload("gh pr create --title test")
    assert _handler(payload) is None


# ---------------------------------------------------------------------------
# Authoritative doc exists at the cited path
# ---------------------------------------------------------------------------


def test_authority_doc_exists() -> None:
    """The block message cites architecture/agent_pr_discipline_2026_05_09.md.

    The doc must exist or the citation is a dead-link.
    """
    p = REPO_ROOT / "architecture" / "agent_pr_discipline_2026_05_09.md"
    assert p.exists(), f"authoritative doc missing at {p}"
    body = p.read_text()
    # Sanity: doc references the same constants as the hook
    assert "300" in body, "doc must cite the 300-LOC threshold"
    assert "ZEUS_PR_ALLOW_TINY" in body, "doc must document the bypass env"
