# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/05_execution_packets/PACKET_INDEX.md P7
"""Tests for scripts/lore_reverify.py."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lore_reverify import (
    _compute_signature,
    _split_frontmatter,
    _update_frontmatter_field,
    reverify_cards,
    main,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_lore_dir(tmp_path: Path) -> Path:
    lore = tmp_path / "docs" / "lore"
    lore.mkdir(parents=True)
    (lore / "_drafts").mkdir()
    (lore / "retired").mkdir()
    (lore / "topology").mkdir()
    return lore


def _card_with_cmd(cmd: str, expected_sig: str = "") -> str:
    sig_line = f"\nexpected_signature: {expected_sig}" if expected_sig else ""
    return f"""\
---
id: 20260515-reverify-test
title: Reverify test card
topic: topology
extracted_from: docs/operations/task_test/PLAN.md:1
extracted_on: 2026-05-15
status: ACTIVE
authority_class: HARD_RULE
last_verified: 2026-05-15
verification_command: {cmd}{sig_line}
---

# Reverify test card

## What

A test card with verification command.
"""


def _card_no_cmd() -> str:
    return """\
---
id: 20260515-no-cmd
title: No verification command
topic: topology
extracted_from: docs/operations/task_test/PLAN.md:1
extracted_on: 2026-05-15
status: ACTIVE
authority_class: HARD_RULE
last_verified: 2026-05-15
---

# No verification command card
"""


# ---------------------------------------------------------------------------
# _compute_signature tests
# ---------------------------------------------------------------------------


def test_compute_signature_deterministic():
    s1 = _compute_signature("hello\nworld\n")
    s2 = _compute_signature("hello\nworld\n")
    assert s1 == s2


def test_compute_signature_strips_trailing_whitespace():
    # Trailing spaces on lines should not affect signature
    s1 = _compute_signature("hello   \nworld\n")
    s2 = _compute_signature("hello\nworld\n")
    assert s1 == s2


def test_compute_signature_is_sha256_hex():
    sig = _compute_signature("test output\n")
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)


def test_compute_signature_differs_for_different_output():
    s1 = _compute_signature("output A\n")
    s2 = _compute_signature("output B\n")
    assert s1 != s2


# ---------------------------------------------------------------------------
# _split_frontmatter tests
# ---------------------------------------------------------------------------


def test_split_frontmatter_valid():
    text = "---\nid: test\n---\n# Body\n"
    fm, raw, body = _split_frontmatter(text)
    assert fm is not None
    assert fm["id"] == "test"
    assert "Body" in body


def test_split_frontmatter_no_delimiters():
    text = "# No frontmatter\n"
    fm, raw, body = _split_frontmatter(text)
    assert fm is None


# ---------------------------------------------------------------------------
# _update_frontmatter_field tests
# ---------------------------------------------------------------------------


def test_update_existing_field():
    text = "---\nstatus: ACTIVE\nid: x\n---\n# body\n"
    updated = _update_frontmatter_field(text, "status", "NEEDS_RE_VERIFICATION")
    fm, _, _ = _split_frontmatter(updated)
    assert fm["status"] == "NEEDS_RE_VERIFICATION"


def test_update_appends_missing_field():
    text = "---\nid: x\ntitle: X\n---\n# body\n"
    updated = _update_frontmatter_field(text, "expected_signature", "abc123")
    fm, _, _ = _split_frontmatter(updated)
    assert fm.get("expected_signature") == "abc123"


def test_update_preserves_body():
    text = "---\nstatus: ACTIVE\n---\n# Important body\n\nContent here.\n"
    updated = _update_frontmatter_field(text, "status", "NEEDS_RE_VERIFICATION")
    assert "Important body" in updated
    assert "Content here" in updated


# ---------------------------------------------------------------------------
# reverify_cards tests
# ---------------------------------------------------------------------------


def test_reverify_skips_cards_without_cmd(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "topology" / "20260515-no-cmd.md").write_text(_card_no_cmd())

    results = reverify_cards(lore, timeout=10, dry_run=False)
    assert results == []


def test_reverify_dry_run_returns_skipped(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "topology" / "20260515-cmd.md").write_text(_card_with_cmd("echo hello"))

    results = reverify_cards(lore, timeout=10, dry_run=True)
    assert len(results) == 1
    assert results[0].outcome == "skipped"
    assert "dry-run" in results[0].message


def test_reverify_records_signature_on_first_run(tmp_path):
    lore = _make_lore_dir(tmp_path)
    card_path = lore / "topology" / "20260515-cmd.md"
    card_path.write_text(_card_with_cmd("echo hello"))

    results = reverify_cards(lore, timeout=10, dry_run=False)
    assert len(results) == 1
    assert results[0].outcome == "recorded"
    # Signature should now be in the card's frontmatter
    updated_text = card_path.read_text()
    fm, _, _ = _split_frontmatter(updated_text)
    assert "expected_signature" in fm
    assert len(fm["expected_signature"]) == 64  # SHA256 hex


def test_reverify_ok_when_signatures_match(tmp_path):
    lore = _make_lore_dir(tmp_path)
    # Compute expected sig for 'echo hello' output
    import subprocess
    result = subprocess.run(["echo", "hello"], capture_output=True, text=True)
    expected_sig = _compute_signature(result.stdout)

    card_path = lore / "topology" / "20260515-cmd.md"
    card_path.write_text(_card_with_cmd("echo hello", expected_sig=expected_sig))

    results = reverify_cards(lore, timeout=10, dry_run=False)
    assert len(results) == 1
    assert results[0].outcome == "ok"


def test_reverify_mismatch_flips_status(tmp_path):
    lore = _make_lore_dir(tmp_path)
    # Use a wrong expected signature
    card_path = lore / "topology" / "20260515-cmd.md"
    card_path.write_text(_card_with_cmd("echo hello", expected_sig="a" * 64))

    results = reverify_cards(lore, timeout=10, dry_run=False)
    assert len(results) == 1
    assert results[0].outcome == "mismatch"

    # Status should now be NEEDS_RE_VERIFICATION in frontmatter
    updated_text = card_path.read_text()
    fm, _, _ = _split_frontmatter(updated_text)
    assert fm["status"] == "NEEDS_RE_VERIFICATION"


def test_reverify_error_on_bad_command(tmp_path):
    lore = _make_lore_dir(tmp_path)
    card_path = lore / "topology" / "20260515-cmd.md"
    card_path.write_text(_card_with_cmd("this_command_definitely_does_not_exist_xyz"))

    results = reverify_cards(lore, timeout=10, dry_run=False)
    assert len(results) == 1
    assert results[0].outcome == "error"


def test_reverify_skips_drafts_and_retired(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "_drafts" / "20260515-draft.md").write_text(_card_with_cmd("echo hello"))
    (lore / "retired" / "20260515-old.md").write_text(_card_with_cmd("echo hello"))

    results = reverify_cards(lore, timeout=10, dry_run=False)
    assert results == []


# ---------------------------------------------------------------------------
# main() CLI tests
# ---------------------------------------------------------------------------


def test_main_dry_run_passes(tmp_path):
    lore = _make_lore_dir(tmp_path)
    (lore / "topology" / "20260515-cmd.md").write_text(_card_with_cmd("echo hello"))

    rc = main(["--lore-root", str(lore), "--dry-run"])
    assert rc == 0


def test_main_empty_lore_zero_exit(tmp_path):
    lore = _make_lore_dir(tmp_path)
    rc = main(["--lore-root", str(lore)])
    assert rc == 0


def test_main_strict_fails_on_mismatch(tmp_path):
    lore = _make_lore_dir(tmp_path)
    card_path = lore / "topology" / "20260515-cmd.md"
    card_path.write_text(_card_with_cmd("echo hello", expected_sig="b" * 64))

    rc = main(["--lore-root", str(lore), "--strict"])
    assert rc == 1


def test_main_strict_passes_when_ok(tmp_path):
    lore = _make_lore_dir(tmp_path)
    import subprocess
    result = subprocess.run(["echo", "hello"], capture_output=True, text=True)
    expected_sig = _compute_signature(result.stdout)

    card_path = lore / "topology" / "20260515-cmd.md"
    card_path.write_text(_card_with_cmd("echo hello", expected_sig=expected_sig))

    rc = main(["--lore-root", str(lore), "--strict"])
    assert rc == 0
