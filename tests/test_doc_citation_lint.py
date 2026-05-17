#!/usr/bin/env python3
# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-17_docs_taxonomy_design/SCAFFOLD.md §4 FM-01/FM-04
#                  + EXECUTION_PLAN.md §2 W2 sub-step A (synthetic-failing fixture corpus)
"""Tests for scripts/doc_citation_lint.py.

Fixture corpus covers:
  happy          — valid cite marker, sha matches, line in bounds
  stale_sha      — sha in marker does not match current HEAD sha
  missing_line   — line number exceeds file length
  wrong_format   — marker does not match expected pattern (lint ignores; bare cite undetected)
  malformed_triplet — marker partially formed; MARKER_RE won't match
  file_not_exists — cited file absent from git HEAD

Each failure-mode fixture file contains a <!-- cite: ... --> marker that the
lint should catch (or correctly ignore, documented per case).
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Adjust import path so tests can import scripts/ directly
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from doc_citation_lint import (
    BARE_CITE_RE,
    MARKER_RE,
    CitationError,
    Citation,
    build_retro_cite_marker,
    collect_files,
    enumerate_bare_cites,
    enumerate_markers,
    lint_files,
    main,
    validate_citation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent


def make_md_file(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(textwrap.dedent(content), encoding="utf-8")
    return f


def fake_sha8_for(path: str) -> str:
    """Return a deterministic fake sha8 for a path string."""
    import hashlib
    return hashlib.sha256(path.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Fixture: HAPPY PATH — valid citation
# ---------------------------------------------------------------------------


def test_happy_path_valid_citation(tmp_path):
    """A cite marker with matching sha and in-bounds line passes with no errors."""
    # We'll mock compute_sha8 and count_lines to return controlled values
    cite_path = "scripts/topology_doctor.py"
    cite_line = 34
    cite_sha = "abcd1234"

    md = make_md_file(tmp_path, "happy.md", f"""\
        # Happy path

        <!-- cite: {cite_path}:{cite_line} sha={cite_sha} -->
        The topology doctor loads topology at line {cite_line}.
    """)

    with (
        patch("doc_citation_lint.compute_sha8", return_value=cite_sha),
        patch("doc_citation_lint.count_lines", return_value=200),
    ):
        errors = lint_files([str(md)], REPO_ROOT)

    assert errors == [], f"Expected no errors, got: {errors}"


# ---------------------------------------------------------------------------
# Fixture: STALE SHA — sha mismatch
# ---------------------------------------------------------------------------


def test_stale_sha_detected(tmp_path):
    """A cite marker whose sha no longer matches the current HEAD sha is flagged."""
    cite_path = "architecture/naming_conventions.yaml"
    cite_line = 140
    stored_sha = "deadbeef"
    actual_sha = "12345678"  # different

    md = make_md_file(tmp_path, "stale_sha.md", f"""\
        # Stale sha fixture

        <!-- cite: {cite_path}:{cite_line} sha={stored_sha} -->
        archive_pattern at line {cite_line}.
    """)

    with (
        patch("doc_citation_lint.compute_sha8", return_value=actual_sha),
        patch("doc_citation_lint.count_lines", return_value=200),
    ):
        errors = lint_files([str(md)], REPO_ROOT)

    assert len(errors) == 1
    err = errors[0]
    assert err.kind == "stale_sha"
    assert err.sha == stored_sha
    assert "deadbeef" in err.detail
    assert "12345678" in err.detail


# ---------------------------------------------------------------------------
# Fixture: MISSING LINE — line number out of range
# ---------------------------------------------------------------------------


def test_missing_line_detected(tmp_path):
    """A cite marker pointing to a line beyond the file's end is flagged."""
    cite_path = "scripts/doc_citation_lint.py"
    cite_line = 9999  # way beyond any real file
    cite_sha = "aabbccdd"

    md = make_md_file(tmp_path, "missing_line.md", f"""\
        # Missing line fixture

        <!-- cite: {cite_path}:{cite_line} sha={cite_sha} -->
        This cite points past end of file.
    """)

    with (
        patch("doc_citation_lint.compute_sha8", return_value=cite_sha),
        patch("doc_citation_lint.count_lines", return_value=50),  # file only 50 lines
    ):
        errors = lint_files([str(md)], REPO_ROOT)

    assert len(errors) == 1
    err = errors[0]
    assert err.kind == "line_out_of_range"
    assert "9999" in err.detail
    assert "50" in err.detail


# ---------------------------------------------------------------------------
# Fixture: WRONG FORMAT — no structured marker, bare cite present
# ---------------------------------------------------------------------------


def test_wrong_format_bare_cite_not_caught_by_lint(tmp_path):
    """Bare path:line cites without a <!-- cite: --> marker are NOT caught by lint.

    Lint only validates existing markers; enumeration of bare cites is the
    retro-cite pass (enumerate_bare_cites). This test documents the boundary.
    """
    md = make_md_file(tmp_path, "wrong_format.md", """\
        # Wrong format fixture

        The loader is at scripts/topology_doctor.py:34 in the source.
        No marker placed above this line.
    """)

    # Lint finds zero markers → zero errors (bare cite is not a lint error)
    errors = lint_files([str(md)], REPO_ROOT)
    assert errors == []

    # But enumerate_bare_cites DOES find it
    bare = enumerate_bare_cites(md)
    assert len(bare) == 1
    assert bare[0][2] == "scripts/topology_doctor.py"
    assert bare[0][3] == "34"


# ---------------------------------------------------------------------------
# Fixture: MALFORMED TRIPLET — marker present but won't parse
# ---------------------------------------------------------------------------


def test_malformed_triplet_not_matched(tmp_path):
    """A malformed <!-- cite: --> marker that doesn't match MARKER_RE is silently skipped.

    The marker format requires exactly: path:LINE sha=SHA8 (8 hex digits).
    Partial or wrong-format markers are treated as prose.
    """
    md = make_md_file(tmp_path, "malformed_triplet.md", """\
        # Malformed triplet fixture

        <!-- cite: scripts/foo.py sha=nohexhere -->
        Missing line number — marker won't parse.

        <!-- cite: scripts/bar.py:42 sha=toolong123456 -->
        sha is 12 chars, not 8 — marker won't parse.
    """)

    # Neither malformed marker parses → zero errors
    errors = lint_files([str(md)], REPO_ROOT)
    assert errors == []

    # Confirm MARKER_RE doesn't match these
    content = md.read_text()
    markers = list(MARKER_RE.finditer(content))
    assert len(markers) == 0, f"Expected no marker matches, got {len(markers)}"


# ---------------------------------------------------------------------------
# Fixture: FILE NOT EXISTS — cited file absent from HEAD
# ---------------------------------------------------------------------------


def test_file_not_exists_detected(tmp_path):
    """A cite marker pointing to a file not tracked by git is flagged as missing."""
    cite_path = "scripts/nonexistent_module.py"
    cite_line = 10
    cite_sha = "00000000"

    md = make_md_file(tmp_path, "file_not_exists.md", f"""\
        # File not exists fixture

        <!-- cite: {cite_path}:{cite_line} sha={cite_sha} -->
        This file does not exist in the repo.
    """)

    # compute_sha8 returns None when git show fails
    with patch("doc_citation_lint.compute_sha8", return_value=None):
        errors = lint_files([str(md)], REPO_ROOT)

    assert len(errors) == 1
    err = errors[0]
    assert err.kind == "missing_file"
    assert cite_path in err.detail


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


def test_marker_re_matches_valid_format():
    """MARKER_RE correctly parses well-formed cite markers."""
    line = "<!-- cite: scripts/topology_doctor.py:34 sha=58ec7cab -->"
    m = MARKER_RE.match(line)
    assert m is not None
    assert m.group("path") == "scripts/topology_doctor.py"
    assert m.group("line") == "34"
    assert m.group("sha") == "58ec7cab"


def test_marker_re_rejects_missing_line():
    """MARKER_RE rejects marker without line number."""
    line = "<!-- cite: scripts/foo.py sha=abcd1234 -->"
    assert MARKER_RE.match(line) is None


def test_marker_re_rejects_wrong_sha_length():
    """MARKER_RE rejects sha that is not exactly 8 hex chars."""
    line = "<!-- cite: scripts/foo.py:10 sha=abc -->"
    assert MARKER_RE.match(line) is None
    line2 = "<!-- cite: scripts/foo.py:10 sha=abcd12345678 -->"
    assert MARKER_RE.match(line2) is None


def test_bare_cite_re_matches_real_patterns():
    """BARE_CITE_RE matches the real bare cite patterns found in design-packet files."""
    cases = [
        ("topology_doctor.py:11", "topology_doctor.py", "11"),
        ("architecture/naming_conventions.yaml:140", "architecture/naming_conventions.yaml", "140"),
        ("docs/authority/AGENTS.md:10-13", "docs/authority/AGENTS.md", "10-13"),
        ("maintenance_worker/core/archival_check_0.py:6", "maintenance_worker/core/archival_check_0.py", "6"),
    ]
    for text, expected_path, expected_line in cases:
        m = BARE_CITE_RE.search(text)
        assert m is not None, f"No match for: {text}"
        assert m.group(1) == expected_path, f"path mismatch: {m.group(1)!r} != {expected_path!r}"
        assert m.group(2) == expected_line, f"line mismatch: {m.group(2)!r} != {expected_line!r}"


def test_bare_cite_re_does_not_match_headings(tmp_path):
    """enumerate_bare_cites skips lines starting with # (headings)."""
    md = make_md_file(tmp_path, "headings.md", """\
        # scripts/topology_doctor.py:34 is not a cite

        Real cite: scripts/topology_doctor.py:34 here.
    """)
    bare = enumerate_bare_cites(md)
    # Only the non-heading line should match
    assert len(bare) == 1
    assert bare[0][2] == "scripts/topology_doctor.py"


def test_bare_cite_re_skips_existing_markers(tmp_path):
    """enumerate_bare_cites skips lines that already have <!-- cite: --> markers."""
    md = make_md_file(tmp_path, "already_marked.md", """\
        <!-- cite: scripts/topology_doctor.py:34 sha=58ec7cab -->
        The loader at scripts/topology_doctor.py:34 is here.
    """)
    bare = enumerate_bare_cites(md)
    # The marker line is skipped; the prose line below has a bare cite
    assert len(bare) == 1
    assert bare[0][0] == 2  # line 2 (prose line)


def test_enumerate_markers_yields_citations(tmp_path):
    """enumerate_markers correctly yields Citation objects from a file."""
    md = make_md_file(tmp_path, "multi.md", """\
        # Multi-cite file

        <!-- cite: scripts/foo.py:10 sha=aaaabbbb -->
        Some text referencing foo.py line 10.

        <!-- cite: architecture/bar.yaml:55 sha=ccccdddd -->
        Some text referencing bar.yaml line 55.
    """)
    cites = list(enumerate_markers(md))
    assert len(cites) == 2
    assert cites[0].path == "scripts/foo.py"
    assert cites[0].line == 10
    assert cites[0].sha == "aaaabbbb"
    assert cites[1].path == "architecture/bar.yaml"
    assert cites[1].line == 55
    assert cites[1].sha == "ccccdddd"


def test_collect_files_expands_directory(tmp_path):
    """collect_files recursively finds .md, .py, .yaml, .yml, .json files in a directory."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "a.md").write_text("a")
    (sub / "b.md").write_text("b")
    (tmp_path / "c.py").write_text("c")  # should be included now
    (sub / "d.yaml").write_text("d")
    (sub / "e.yml").write_text("e")
    (sub / "f.json").write_text("f")
    (tmp_path / "g.txt").write_text("g")  # should be excluded

    files = collect_files([str(tmp_path)], REPO_ROOT)
    names = {f.name for f in files}
    assert "a.md" in names
    assert "b.md" in names
    assert "c.py" in names
    assert "d.yaml" in names
    assert "e.yml" in names
    assert "f.json" in names
    assert "g.txt" not in names


def test_main_exit_zero_no_errors(tmp_path):
    """main() returns 0 when no cite markers are present."""
    md = tmp_path / "clean.md"
    md.write_text("# No cites here\n\nJust prose.\n")
    rc = main([str(md)])
    assert rc == 0


def test_main_exit_nonzero_on_error(tmp_path):
    """main() returns non-zero when a stale sha is detected."""
    md = make_md_file(tmp_path, "bad.md", """\
        <!-- cite: scripts/doc_citation_lint.py:1 sha=deadbeef -->
        Some prose.
    """)
    with patch("doc_citation_lint.compute_sha8", return_value="aaaabbbb"):
        rc = main([str(md)])
    assert rc != 0


def test_build_retro_cite_marker_returns_none_for_missing_file():
    """build_retro_cite_marker returns None when file is not in HEAD."""
    with patch("doc_citation_lint.compute_sha8", return_value=None):
        result = build_retro_cite_marker("nonexistent.py", "42", REPO_ROOT)
    assert result is None


def test_build_retro_cite_marker_format():
    """build_retro_cite_marker produces correct marker format."""
    with patch("doc_citation_lint.compute_sha8", return_value="58ec7cab"):
        result = build_retro_cite_marker("scripts/topology_doctor.py", "34", REPO_ROOT)
    assert result == "<!-- cite: scripts/topology_doctor.py:34 sha=58ec7cab -->"


def test_build_retro_cite_marker_range_uses_start_line():
    """build_retro_cite_marker with a range spec uses the start line."""
    with patch("doc_citation_lint.compute_sha8", return_value="12345678"):
        result = build_retro_cite_marker("docs/authority/AGENTS.md", "10-13", REPO_ROOT)
    assert result == "<!-- cite: docs/authority/AGENTS.md:10 sha=12345678 -->"
