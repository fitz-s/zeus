# Created: 2026-05-09
# Last reused or audited: 2026-05-09
# Authority basis: operator directive 2026-05-09 — workflow redesign plan §3.6
"""Structural antibody tests for architecture/agent_pr_discipline_2026_05_09.md.

These tests catch regressions in the authority doc structure, ensuring:
- Four Principle headings present, in order, with canonical names
- All four memory entry filenames referenced in the doc
- No reference to the deleted pr_lifecycle_2026_05_09.md file
- No architecture doc anywhere references the deleted file

They do NOT test behavior — that is covered by
tests/test_pre_merge_comment_check.py and
tests/test_pr_create_loc_accumulation_hook.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTHORITY_DOC = REPO_ROOT / "architecture" / "agent_pr_discipline_2026_05_09.md"
ARCHITECTURE_DIR = REPO_ROOT / "architecture"
DELETED_DOC = "pr_lifecycle_2026_05_09.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc_text() -> str:
    assert AUTHORITY_DOC.exists(), f"Authority doc missing: {AUTHORITY_DOC}"
    return AUTHORITY_DOC.read_text()


# ---------------------------------------------------------------------------
# Four-principle heading structure
# ---------------------------------------------------------------------------

CANONICAL_PRINCIPLE_NAMES = [
    "Coherent unit of work, not LOC count",
    "Bot comments are bug reports; the fix-commit IS the response",
    "Original-executor continuity",
    "Teach the reasoning, not the rule",
]


class TestFourPrincipleHeadings:
    def test_exactly_four_principle_headings(self):
        """Authority doc must have exactly four '## Principle N —' headings."""
        text = _doc_text()
        # Match lines like "## Principle 1 —" or "## Principle 1 —" (em-dash variants)
        headings = re.findall(r"^##\s+Principle\s+\d+\s+[—–-]", text, re.MULTILINE)
        assert len(headings) == 4, (
            f"Expected exactly 4 Principle headings, found {len(headings)}: {headings}"
        )

    def test_principles_in_order_1_through_4(self):
        """Principle headings must appear in numeric order 1, 2, 3, 4."""
        text = _doc_text()
        numbers = re.findall(r"^##\s+Principle\s+(\d+)\s+[—–-]", text, re.MULTILINE)
        assert numbers == ["1", "2", "3", "4"], (
            f"Principle headings must be in order [1,2,3,4]; found {numbers}"
        )

    @pytest.mark.parametrize("n,name", enumerate(CANONICAL_PRINCIPLE_NAMES, start=1))
    def test_principle_canonical_name(self, n: int, name: str):
        """Each Principle N heading must contain the canonical name."""
        text = _doc_text()
        # Find the heading for principle N
        pattern = rf"^##\s+Principle\s+{n}\s+[—–-]\s*(.+)$"
        m = re.search(pattern, text, re.MULTILINE)
        assert m is not None, f"No '## Principle {n} —' heading found in authority doc"
        heading_text = m.group(0)
        # Check canonical name fragment appears in heading
        # Use a key fragment to tolerate minor wording differences
        key_fragments = {
            1: ("Coherent unit", "not LOC count"),
            2: ("fix-commit IS the response",),
            3: ("Original-executor continuity",),
            4: ("Teach the reasoning",),
        }
        for fragment in key_fragments[n]:
            assert fragment in text, (
                f"Principle {n} section must contain {fragment!r}; "
                f"check authority doc for drift from canonical name.\n"
                f"Heading found: {heading_text!r}"
            )


# ---------------------------------------------------------------------------
# Memory entry filenames referenced
# ---------------------------------------------------------------------------

EXPECTED_MEMORY_ENTRIES = [
    "feedback_pr_300_loc_threshold_with_education.md",
    "feedback_pr_unit_of_work_not_loc.md",
    "feedback_pr_bot_comments_are_bug_reports.md",
    "feedback_pr_original_executor_continuity.md",
]


class TestMemoryEntryReferences:
    @pytest.mark.parametrize("entry", EXPECTED_MEMORY_ENTRIES)
    def test_memory_entry_referenced_in_doc(self, entry: str):
        """Authority doc must reference each of the four memory entry filenames."""
        text = _doc_text()
        assert entry in text, (
            f"Authority doc must reference memory entry {entry!r}; "
            f"add it to the Memory entry pointers section."
        )


# ---------------------------------------------------------------------------
# Deleted doc not referenced
# ---------------------------------------------------------------------------

class TestBackstopHooksSection:
    """Authority doc must document all three backstop hooks."""

    EXPECTED_HOOKS = [
        "pr_create_loc_accumulation",
        "pre_merge_comment_check",
        "pr_thread_reply_waste",
    ]

    def test_exactly_three_backstop_hooks_documented(self):
        """Backstop hooks section must reference all three hook ids."""
        text = _doc_text()
        missing = [h for h in self.EXPECTED_HOOKS if h not in text]
        assert not missing, (
            f"Authority doc missing backstop hook reference(s): {missing}\n"
            f"Add a '### `<hook_id>`' entry in the Backstop hooks section."
        )

    @pytest.mark.parametrize("hook_id", EXPECTED_HOOKS)
    def test_backstop_hook_referenced(self, hook_id: str):
        """Each backstop hook id must appear in the authority doc."""
        assert hook_id in _doc_text(), (
            f"Hook '{hook_id}' not referenced in authority doc backstop section."
        )


class TestDeletedDocNotReferenced:
    def test_authority_doc_does_not_reference_deleted_lifecycle(self):
        """Authority doc must NOT reference the deleted pr_lifecycle_2026_05_09.md."""
        text = _doc_text()
        assert DELETED_DOC not in text, (
            f"Authority doc references the deleted file {DELETED_DOC!r}; remove the reference."
        )

    def test_no_architecture_doc_references_deleted_lifecycle(self):
        """No file in architecture/ may reference the deleted pr_lifecycle doc."""
        offenders: list[str] = []
        for md_file in ARCHITECTURE_DIR.glob("*.md"):
            if md_file.name == DELETED_DOC:
                # The deleted file itself (if somehow recreated) would be a problem,
                # but we're checking references FROM other docs, not the file itself.
                continue
            content = md_file.read_text()
            if DELETED_DOC in content:
                offenders.append(str(md_file.relative_to(REPO_ROOT)))
        assert not offenders, (
            f"These architecture docs reference the deleted {DELETED_DOC!r}:\n"
            + "\n".join(f"  {o}" for o in offenders)
        )

    def test_deleted_lifecycle_file_does_not_exist(self):
        """The deleted pr_lifecycle_2026_05_09.md file must not exist on disk."""
        deleted_path = ARCHITECTURE_DIR / DELETED_DOC
        assert not deleted_path.exists(), (
            f"Deleted file {deleted_path} has been recreated; remove it."
        )
