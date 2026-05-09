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


# ---------------------------------------------------------------------------
# Four-principle block message — redesign antibodies (2026-05-09)
# ---------------------------------------------------------------------------


def test_block_message_lists_all_four_principles(monkeypatch, capsys) -> None:
    """Block message must name all four principles by label (P1-P4).

    Redesign requirement: the pr_create gate message surfaces sibling
    principles P2/P3/P4 so the agent reasons about the full workflow,
    not just the LOC threshold.
    """
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
    assert result == _BLOCK

    # Each principle must appear by name in the message
    import re
    for label in ("P2", "P3", "P4"):
        assert label in msg, (
            f"Block message must reference principle {label!r}; "
            f"message excerpt:\n{msg[:1200]}"
        )
    # Principle 1 is the gate itself — the message must name Principle 1 as well
    assert "Principle 1" in msg or "P1" in msg, (
        f"Block message must reference Principle 1 framing; "
        f"message excerpt:\n{msg[:1200]}"
    )


def test_block_message_does_not_reference_deleted_lifecycle_doc(monkeypatch, capsys) -> None:
    """Block message must NOT reference pr_lifecycle_2026_05_09.md (deleted doc).

    That file has been removed; any surviving reference is a dead citation.
    """
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (50, 50, 1),
    )
    payload = _mk_payload("gh pr create --title test --body test")
    _handler(payload)
    captured = capsys.readouterr()
    msg = captured.err
    assert "pr_lifecycle_2026_05_09" not in msg, (
        "Block message references the deleted pr_lifecycle doc; remove the reference.\n"
        f"message excerpt:\n{msg[:1200]}"
    )


def test_block_message_lists_four_memory_entry_filenames(monkeypatch, capsys) -> None:
    """Block message must list all four memory entry filenames.

    Redesign requirement: the memory section replaces the single
    'feedback_pr_300_loc_threshold_with_education.md' reference with
    all four entries so a fresh-session agent knows the full memory surface.
    """
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (50, 50, 1),
    )
    payload = _mk_payload("gh pr create --title test --body test")
    _handler(payload)
    captured = capsys.readouterr()
    msg = captured.err

    expected_entries = [
        "feedback_pr_300_loc_threshold_with_education.md",
        "feedback_pr_unit_of_work_not_loc.md",
        "feedback_pr_bot_comments_are_bug_reports.md",
        "feedback_pr_original_executor_continuity.md",
    ]
    for entry in expected_entries:
        assert entry in msg, (
            f"Block message missing memory entry {entry!r}.\n"
            f"message excerpt:\n{msg[:1500]}"
        )


# ---------------------------------------------------------------------------
# Finding 1 (Codex P2): trailer-only author detection
# ---------------------------------------------------------------------------


def test_author_detection_ignores_quoted_trailer_in_body() -> None:
    """An operator commit that QUOTES the trailer in body text must not be
    classified as agent contribution.

    Regression anchor for Codex P2 finding: substring match anywhere in the
    commit body misclassified operator discussion commits as agent commits.
    The fix restricts the match to the trailer section only (last paragraph).
    """
    src = (HOOK_DIR / "dispatch.py").read_text()
    # Locate _agent_authored_loc_in_range body
    start = src.index("def _agent_authored_loc_in_range")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    # Must NOT use a bare substring match across the entire commit body
    assert '"Co-Authored-By: Claude" in body' not in body, (
        "Bare substring match across full commit body still present — "
        "fix must restrict to trailer section only."
    )
    # Must use paragraph/trailer-block logic
    assert "paragraphs" in body or "trailer" in body.lower(), (
        "author detection does not appear to use trailer-block logic; "
        "expected 'paragraphs' or 'trailer' in the handler."
    )


def test_author_detection_trailer_classification() -> None:
    """Directly test the paragraph-split trailer logic embedded in the source.

    Simulates what _agent_authored_loc_in_range does per commit body:
    - body with Co-Authored-By only in middle paragraph → NOT agent
    - body with Co-Authored-By in last paragraph (trailer) → IS agent
    """

    def _is_agent_commit(body: str) -> bool:
        """Replicate the trailer-block classification from dispatch.py."""
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
        trailer_block = paragraphs[-1] if paragraphs else ""
        trailer_lines = [ln.strip() for ln in trailer_block.splitlines()]
        return any(
            ln.startswith("Co-Authored-By:") and "Claude" in ln
            for ln in trailer_lines
        )

    # Operator commit quoting the trailer in discussion text — must NOT match
    operator_body_with_quote = (
        "Reverts the bad agent commit.\n\n"
        "Do not use Co-Authored-By: Claude style trailers in operator commits.\n\n"
        "Signed-off-by: Fitz <fitz@example.com>"
    )
    assert not _is_agent_commit(operator_body_with_quote), (
        "Operator commit quoting 'Co-Authored-By: Claude' in body text "
        "was misclassified as agent contribution."
    )

    # Genuine agent commit with trailer in last paragraph — must match
    agent_body = (
        "fix(hooks): improve regex for pr gate\n\n"
        "Extends the alternation to catch inline VAR=val form.\n\n"
        "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
    )
    assert _is_agent_commit(agent_body), (
        "Genuine agent commit with Co-Authored-By trailer not recognised."
    )

    # Agent commit where Co-Authored-By is in a middle paragraph, not trailer — must NOT match
    agent_body_mid = (
        "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>\n\n"
        "Some follow-up note without a trailer."
    )
    assert not _is_agent_commit(agent_body_mid), (
        "Co-Authored-By in a non-trailer (middle) paragraph must not match."
    )


# ---------------------------------------------------------------------------
# Finding 6 (Copilot): inline VAR=val regex coverage
# ---------------------------------------------------------------------------


def test_regex_matches_inline_env_assignment() -> None:
    """ZEUS_PR_ALLOW_TINY=1 gh pr create must be matched by the command regex.

    Regression anchor for Copilot finding 6: the original regex only caught
    `env VAR=val gh pr create` (with the `env` prefix). The more common inline
    form `VAR=val gh pr create` was silently skipped, meaning the bypass
    itself could evade the hook's command detection.
    """
    import re

    pattern = re.compile(
        r"^\s*(?:(?:env\s+)?[A-Z_][A-Z0-9_]*=\S+\s+)*gh\s+pr\s+(create|ready)\b"
    )

    # Inline assignment — must match
    assert pattern.search("ZEUS_PR_ALLOW_TINY=1 gh pr create --title foo"), (
        "Inline VAR=val form not matched by regex."
    )
    # env-prefix form — must still match
    assert pattern.search("env ZEUS_PR_ALLOW_TINY=1 gh pr create --title foo"), (
        "env VAR=val form no longer matched after regex change."
    )
    # Multiple inline vars — must match
    assert pattern.search("A=1 B=2 gh pr create --title foo"), (
        "Multiple inline VAR=val pairs not matched."
    )
    # Plain gh pr create — must match
    assert pattern.search("gh pr create --title foo"), (
        "Plain gh pr create without env prefix no longer matched."
    )
    # gh pr ready — must match
    assert pattern.search("ZEUS_PR_ALLOW_TINY=1 gh pr ready 123"), (
        "gh pr ready with inline var not matched."
    )
    # heredoc body — must NOT match
    assert not pattern.search("cat <<EOF\ngh pr create\nEOF"), (
        "heredoc body falsely matched."
    )


# ---------------------------------------------------------------------------
# Codex P2 fix (2026-05-09): inline bypass assignment must degrade to advisory
# ---------------------------------------------------------------------------


def test_inline_env_bypass_degrades_to_advisory(monkeypatch) -> None:
    """ZEUS_PR_ALLOW_TINY=1 gh pr create must degrade to advisory, not block.

    Regression anchor for Codex P2 finding: when the bypass variable is
    supplied as an inline shell assignment (`ZEUS_PR_ALLOW_TINY=1 gh pr create`)
    rather than a real env export, the hook process does NOT inherit the variable
    via os.environ. The handler must parse inline assignments from the command
    string as a secondary bypass source so the agent's documented bypass path
    actually works.
    """
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)  # no process-env bypass
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (50, 50, 1),  # below threshold — would block without bypass
    )
    # Inline assignment form — bypass must be detected from the command string
    payload = _mk_payload("ZEUS_PR_ALLOW_TINY=1 gh pr create --title test")
    result = _handler(payload)
    assert result is not None, (
        "Inline ZEUS_PR_ALLOW_TINY=1 must degrade to advisory, not return None"
    )
    assert result != _BLOCK, (
        "Inline ZEUS_PR_ALLOW_TINY=1 must not return BLOCK sentinel — "
        "handler must detect the bypass from the command string, not only os.environ"
    )
    assert "ADVISORY" in result, f"Bypass result must be advisory text; got: {result!r}"


def test_env_prefix_bypass_degrades_to_advisory(monkeypatch) -> None:
    """env ZEUS_PR_ALLOW_TINY=1 gh pr create must also degrade to advisory.

    Covers the `env VAR=val cmd` shell form in addition to the bare inline form.
    """
    monkeypatch.delenv("ZEUS_PR_ALLOW_TINY", raising=False)
    monkeypatch.setattr(
        dispatch,
        "_agent_authored_loc_in_range",
        lambda mb, head: (50, 50, 1),
    )
    payload = _mk_payload("env ZEUS_PR_ALLOW_TINY=1 gh pr create --title test")
    result = _handler(payload)
    assert result is not None
    assert result != _BLOCK, (
        "env ZEUS_PR_ALLOW_TINY=1 form must not block — inline env parse must catch it"
    )
    assert "ADVISORY" in result
