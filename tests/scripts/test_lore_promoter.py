# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/05_execution_packets/PACKET_INDEX.md P7
"""Tests for scripts/lore_promoter.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lore_promoter import (
    cmd_promote,
    cmd_list_drafts,
    main,
    REQUIRED_FIELDS,
    VALID_TOPICS,
    _parse_frontmatter,
    _validate_frontmatter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_DRAFT = """\
---
id: 20260515-my-draft
title: My draft lore card
topic: topology
extracted_from: docs/operations/task_test/PLAN.md:1-5
extracted_on: 2026-05-15
status: ACTIVE
authority_class: DESIGN_RATIONALE
last_verified: 2026-05-15
---

# My draft lore card

## What

A test draft.
"""

DRAFT_MISSING_FIELD = """\
---
id: 20260515-incomplete-draft
title: Incomplete draft
topic: topology
extracted_from: docs/operations/task_test/PLAN.md:1
extracted_on: 2026-05-15
status: ACTIVE
authority_class: HARD_RULE
---
# Missing last_verified
"""

DRAFT_WRONG_TOPIC = """\
---
id: 20260515-wrong-topic-draft
title: Wrong topic draft
topic: hooks
extracted_from: docs/operations/task_test/PLAN.md:1
extracted_on: 2026-05-15
status: ACTIVE
authority_class: HARD_RULE
last_verified: 2026-05-15
---
# Topic is hooks but caller will say topology
"""


def _make_lore_dir(tmp_path: Path) -> Path:
    lore = tmp_path / "docs" / "lore"
    lore.mkdir(parents=True)
    (lore / "_drafts").mkdir()
    (lore / "topology").mkdir()
    return lore


# ---------------------------------------------------------------------------
# _parse_frontmatter tests
# ---------------------------------------------------------------------------


def test_parse_frontmatter_valid():
    fm = _parse_frontmatter(VALID_DRAFT)
    assert fm is not None
    assert fm["id"] == "20260515-my-draft"
    assert fm["topic"] == "topology"


def test_parse_frontmatter_no_delimiters():
    fm = _parse_frontmatter("# No frontmatter\n\nJust body.\n")
    assert fm is None


def test_parse_frontmatter_bad_yaml():
    bad = "---\n: : : invalid yaml\n---\n# body\n"
    fm = _parse_frontmatter(bad)
    assert fm is None


# ---------------------------------------------------------------------------
# _validate_frontmatter tests
# ---------------------------------------------------------------------------


def test_validate_frontmatter_all_required():
    fm = {
        "id": "20260515-x",
        "title": "X",
        "topic": "topology",
        "extracted_from": "somewhere",
        "extracted_on": "2026-05-15",
        "status": "ACTIVE",
        "authority_class": "HARD_RULE",
        "last_verified": "2026-05-15",
    }
    errors = _validate_frontmatter(fm, Path("test.md"))
    assert errors == []


def test_validate_frontmatter_missing_field():
    fm = {
        "id": "20260515-x",
        "title": "X",
        "topic": "topology",
        "extracted_from": "somewhere",
        "extracted_on": "2026-05-15",
        "status": "ACTIVE",
        "authority_class": "HARD_RULE",
        # last_verified missing
    }
    errors = _validate_frontmatter(fm, Path("test.md"))
    assert any("last_verified" in e for e in errors)


def test_validate_frontmatter_invalid_topic():
    fm = {
        "id": "x",
        "title": "X",
        "topic": "not_valid_topic",
        "extracted_from": "x",
        "extracted_on": "2026-05-15",
        "status": "ACTIVE",
        "authority_class": "HARD_RULE",
        "last_verified": "2026-05-15",
    }
    errors = _validate_frontmatter(fm, Path("test.md"))
    assert any("invalid topic" in e for e in errors)


# ---------------------------------------------------------------------------
# cmd_promote tests
# ---------------------------------------------------------------------------


def test_promote_success(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-my-draft.md").write_text(VALID_DRAFT)

    rc = cmd_promote("20260515-my-draft", "topology", lore, dry_run=False)
    assert rc == 0
    assert (lore / "topology" / "20260515-my-draft.md").exists()
    assert not (lore / "_drafts" / "20260515-my-draft.md").exists()


def test_promote_dry_run_does_not_move(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-my-draft.md").write_text(VALID_DRAFT)

    rc = cmd_promote("20260515-my-draft", "topology", lore, dry_run=True)
    assert rc == 0
    # Draft still in place
    assert (lore / "_drafts" / "20260515-my-draft.md").exists()
    # Not promoted
    assert not (lore / "topology" / "20260515-my-draft.md").exists()


def test_promote_creates_dest_dir(tmp_path):
    lore = _make_lore_dir(tmp_path)
    hooks_draft = VALID_DRAFT.replace("topic: topology", "topic: hooks").replace(
        "id: 20260515-my-draft", "id: 20260515-hooks-draft"
    )
    (lore / "_drafts" / "20260515-hooks-draft.md").write_text(hooks_draft)
    # hooks/ does not exist yet
    assert not (lore / "hooks").exists()

    rc = cmd_promote("20260515-hooks-draft", "hooks", lore, dry_run=False)
    assert rc == 0
    assert (lore / "hooks" / "20260515-hooks-draft.md").exists()


def test_promote_rejects_missing_field(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-incomplete-draft.md").write_text(DRAFT_MISSING_FIELD)

    rc = cmd_promote("20260515-incomplete-draft", "topology", lore, dry_run=False)
    assert rc == 1
    # File should NOT have moved
    assert (lore / "_drafts" / "20260515-incomplete-draft.md").exists()


def test_promote_rejects_topic_mismatch(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-wrong-topic-draft.md").write_text(DRAFT_WRONG_TOPIC)

    # Card says topic: hooks, but we pass topology
    rc = cmd_promote("20260515-wrong-topic-draft", "topology", lore, dry_run=False)
    assert rc == 1
    assert (lore / "_drafts" / "20260515-wrong-topic-draft.md").exists()


def test_promote_rejects_nonexistent_draft(tmp_path):
    lore = _make_lore_dir(tmp_path)

    rc = cmd_promote("does-not-exist", "topology", lore, dry_run=False)
    assert rc == 1


def test_promote_rejects_invalid_topic(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-my-draft.md").write_text(VALID_DRAFT)

    rc = cmd_promote("20260515-my-draft", "not_valid_topic", lore, dry_run=False)
    assert rc == 1


def test_promote_rejects_dest_exists(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-my-draft.md").write_text(VALID_DRAFT)
    # Pre-create destination
    (lore / "topology" / "20260515-my-draft.md").write_text("existing card\n")

    rc = cmd_promote("20260515-my-draft", "topology", lore, dry_run=False)
    assert rc == 1


def test_promote_id_without_extension(tmp_path):
    """draft_id without .md suffix should also resolve."""
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-my-draft.md").write_text(VALID_DRAFT)

    rc = cmd_promote("20260515-my-draft", "topology", lore, dry_run=False)
    assert rc == 0


# ---------------------------------------------------------------------------
# cmd_list_drafts tests
# ---------------------------------------------------------------------------


def test_list_drafts_empty(tmp_path):
    lore = _make_lore_dir(tmp_path)
    rc = cmd_list_drafts(lore)
    assert rc == 0


def test_list_drafts_shows_card(tmp_path, capsys):
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-my-draft.md").write_text(VALID_DRAFT)

    rc = cmd_list_drafts(lore)
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260515-my-draft" in out


# ---------------------------------------------------------------------------
# main() CLI tests
# ---------------------------------------------------------------------------


def test_main_help_exit():
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_main_promote_via_cli(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-my-draft.md").write_text(VALID_DRAFT)

    rc = main(["--lore-root", str(lore), "promote", "20260515-my-draft", "topology"])
    assert rc == 0
    assert (lore / "topology" / "20260515-my-draft.md").exists()


def test_main_no_subcommand_returns_zero(tmp_path):
    lore = _make_lore_dir(tmp_path)
    rc = main(["--lore-root", str(lore)])
    assert rc == 0
