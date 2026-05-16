# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/05_execution_packets/PACKET_INDEX.md P7
"""Tests for scripts/lore_indexer.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Allow import from scripts/ even outside the installed package
import sys, os
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lore_indexer import (
    LoreCard,
    ValidationError,
    build_index,
    main,
    walk_lore,
    REQUIRED_FIELDS,
    VALID_TOPICS,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_CARD = """\
---
id: 20260515-test-card
title: Test card for unit tests
topic: topology
extracted_from: docs/operations/task_test/PLAN.md:1-5
extracted_on: 2026-05-15
status: ACTIVE
authority_class: DESIGN_RATIONALE
last_verified: 2026-05-15
---

# Test card for unit tests

## What

This is a test.

## Why

Because tests are good.

## How To Apply

Apply carefully.
"""

VALID_CARD_WITH_OPTIONAL = """\
---
id: 20260515-card-with-optional
title: Card with optional fields
topic: topology
extracted_from: docs/operations/task_test/PLAN.md:10-15
extracted_on: 2026-05-15
status: ACTIVE
authority_class: HARD_RULE
last_verified: 2026-05-15
verification_command: echo ok
related: [20260515-test-card]
---

# Card with optional fields
"""


def _make_lore_dir(tmp_path: Path) -> Path:
    """Create a minimal lore directory structure."""
    lore = tmp_path / "docs" / "lore"
    lore.mkdir(parents=True)
    (lore / "_drafts").mkdir()
    (lore / "retired").mkdir()
    (lore / "topology").mkdir()
    return lore


# ---------------------------------------------------------------------------
# walk_lore tests
# ---------------------------------------------------------------------------


def test_walk_lore_empty_dir(tmp_path):
    lore = _make_lore_dir(tmp_path)
    cards, errors = walk_lore(lore)
    assert cards == []
    assert errors == []


def test_walk_lore_valid_card(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "topology" / "20260515-test-card.md").write_text(MINIMAL_VALID_CARD)

    cards, errors = walk_lore(lore)
    assert errors == [], f"Unexpected errors: {errors}"
    assert len(cards) == 1
    assert cards[0].id == "20260515-test-card"
    assert cards[0].topic == "topology"


def test_walk_lore_skips_drafts(tmp_path):
    lore = _make_lore_dir(tmp_path)
    # Card in _drafts should be excluded from index
    (lore / "_drafts" / "20260515-draft.md").write_text(MINIMAL_VALID_CARD)

    cards, errors = walk_lore(lore)
    assert cards == []
    assert errors == []


def test_walk_lore_skips_retired(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "retired" / "20260515-old.md").write_text(MINIMAL_VALID_CARD)

    cards, errors = walk_lore(lore)
    assert cards == []
    assert errors == []


def test_walk_lore_skips_root_level_md(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "INDEX.md").write_text("# Index\n")
    (lore / "POLICY.md").write_text("# Policy\n")

    cards, errors = walk_lore(lore)
    assert cards == []
    assert errors == []


def test_walk_lore_missing_required_field(tmp_path):
    lore = _make_lore_dir(tmp_path)
    card_text = """\
---
id: 20260515-incomplete
title: Incomplete card
topic: topology
extracted_from: docs/operations/task_test/PLAN.md:1
extracted_on: 2026-05-15
status: ACTIVE
authority_class: HARD_RULE
---
# Missing last_verified
"""
    (lore / "topology" / "20260515-incomplete.md").write_text(card_text)

    cards, errors = walk_lore(lore)
    assert len(errors) == 1
    assert "last_verified" in errors[0].message
    # Card with errors excluded from index
    assert cards == []


def test_walk_lore_missing_frontmatter(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "topology" / "no-fm.md").write_text("# No frontmatter\n\nJust text.\n")

    cards, errors = walk_lore(lore)
    assert len(errors) == 1
    assert "frontmatter" in errors[0].message.lower()
    assert cards == []


def test_walk_lore_invalid_topic(tmp_path):
    lore = _make_lore_dir(tmp_path)
    bad_topic_card = MINIMAL_VALID_CARD.replace("topic: topology", "topic: nonexistent_topic")
    # Also fix topic mismatch by putting it in a dir with the wrong name
    (lore / "topology").mkdir(exist_ok=True)
    (lore / "topology" / "bad-topic.md").write_text(bad_topic_card)

    cards, errors = walk_lore(lore)
    # Should have: invalid topic error + topic/dir mismatch error
    assert len(errors) >= 1
    assert any("invalid topic" in e.message for e in errors)


def test_walk_lore_topic_dir_mismatch(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "hooks").mkdir()
    # Card says topic: topology but lives in hooks/
    (lore / "hooks" / "wrong-topic.md").write_text(MINIMAL_VALID_CARD)  # topic: topology

    cards, errors = walk_lore(lore)
    assert any("does not match containing directory" in e.message for e in errors)


def test_walk_lore_multiple_topics(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "hooks").mkdir()

    hooks_card = MINIMAL_VALID_CARD.replace("topic: topology", "topic: hooks").replace(
        "id: 20260515-test-card", "id: 20260515-hooks-card"
    )
    (lore / "topology" / "20260515-test-card.md").write_text(MINIMAL_VALID_CARD)
    (lore / "hooks" / "20260515-hooks-card.md").write_text(hooks_card)

    cards, errors = walk_lore(lore)
    assert errors == []
    assert len(cards) == 2
    topics = {c.topic for c in cards}
    assert topics == {"topology", "hooks"}


def test_walk_lore_nonexistent_root(tmp_path):
    cards, errors = walk_lore(tmp_path / "no_such_dir")
    assert cards == []
    assert len(errors) == 1
    assert "does not exist" in errors[0].message


# ---------------------------------------------------------------------------
# build_index tests
# ---------------------------------------------------------------------------


def test_build_index_empty():
    idx = build_index([])
    assert idx["schema_version"] == 1
    assert idx["topics"] == {}


def test_build_index_single_card(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "topology" / "20260515-test-card.md").write_text(MINIMAL_VALID_CARD)

    cards, errors = walk_lore(lore)
    assert errors == []
    idx = build_index(cards)
    assert "topology" in idx["topics"]
    assert len(idx["topics"]["topology"]) == 1
    entry = idx["topics"]["topology"][0]
    assert entry["id"] == "20260515-test-card"


def test_build_index_sorted_by_id(tmp_path):
    lore = _make_lore_dir(tmp_path)
    card_b = MINIMAL_VALID_CARD.replace("20260515-test-card", "20260515-b-card")
    card_a = MINIMAL_VALID_CARD.replace("20260515-test-card", "20260515-a-card")
    (lore / "topology" / "20260515-b-card.md").write_text(card_b)
    (lore / "topology" / "20260515-a-card.md").write_text(card_a)

    cards, errors = walk_lore(lore)
    assert errors == []
    idx = build_index(cards)
    ids = [e["id"] for e in idx["topics"]["topology"]]
    assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# main() / CLI tests
# ---------------------------------------------------------------------------


def test_main_validate_only_passes(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "topology" / "20260515-test-card.md").write_text(MINIMAL_VALID_CARD)

    rc = main(["--lore-root", str(lore), "--validate-only"])
    assert rc == 0


def test_main_writes_index(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "topology" / "20260515-test-card.md").write_text(MINIMAL_VALID_CARD)

    out = tmp_path / "INDEX.json"
    rc = main(["--lore-root", str(lore), "--output", str(out)])
    assert rc == 0
    assert out.exists()
    data = json.loads(out.read_text())
    assert "topology" in data["topics"]


def test_main_strict_fails_on_errors(tmp_path):
    lore = _make_lore_dir(tmp_path)
    # Card without required field
    bad_card = """\
---
id: 20260515-bad
title: Bad card
topic: topology
extracted_from: x
extracted_on: 2026-05-15
status: ACTIVE
authority_class: HARD_RULE
---
# Missing last_verified
"""
    (lore / "topology" / "20260515-bad.md").write_text(bad_card)
    rc = main(["--lore-root", str(lore), "--validate-only", "--strict"])
    assert rc == 1


def test_main_empty_lore_root_ok(tmp_path):
    lore = _make_lore_dir(tmp_path)
    out = tmp_path / "INDEX.json"
    rc = main(["--lore-root", str(lore), "--output", str(out)])
    assert rc == 0
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["topics"] == {}
