# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: oracle/Kelly evidence rebuild context capsule §insight-2 +
#                  PR #57 follow-up landing the helper.
"""Self-tests for tests/_helpers/source_grep.py.

Pin the strip's contract: code-vs-comment-vs-string discrimination,
line/column preservation, and the find_forbidden_assignments wrapper.

These are the antibodies for the antibody helper — if these go red,
every downstream source-grep test risks silent false-positive or
false-negative.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from tests._helpers.source_grep import (
    find_all_in_code,
    find_forbidden_assignments,
    strip_python_comments_and_strings,
)


def test_strip_blanks_pound_comments_to_end_of_line():
    src = "x = 1  # this comment must vanish\ny = 2\n"
    out = strip_python_comments_and_strings(src)
    # Comment chars replaced with spaces, code preserved
    assert "comment" not in out
    assert out.startswith("x = 1  ")
    # Newline preserved → line numbers stable
    assert out.count("\n") == src.count("\n")
    # Code-only re-search must find both assignments
    assert re.search(r"^x = 1", out, re.MULTILINE)
    assert re.search(r"^y = 2", out, re.MULTILINE)


def test_strip_preserves_single_line_strings():
    """Contract: single-line string literals are kept verbatim so
    antibodies can match against their content. Only triple-quoted
    strings (the docstring vehicle) get blanked."""
    src = 'name = "settlement_capture"\n'
    out = strip_python_comments_and_strings(src)
    # The literal stays intact — antibodies that ban a specific value
    # depend on this. e.g. forbidding `_phase_source = "verified_gamma"`
    # requires the literal to be searchable.
    assert "settlement_capture" in out
    assert 'name = "settlement_capture"' in out


def test_strip_does_not_treat_pound_inside_single_string_as_comment():
    """A `#` inside a single-quoted string is data, not a comment.
    The strip must scan past the closing quote before re-enabling
    comment detection."""
    src = 'msg = "look #at this"  # real comment\nx = 1\n'
    out = strip_python_comments_and_strings(src)
    # The string literal is preserved
    assert 'msg = "look #at this"' in out
    # The real comment IS blanked
    assert "real comment" not in out
    # Code below remains
    assert "x = 1" in out


def test_strip_blanks_triple_quoted_docstring_spanning_lines():
    src = textwrap.dedent('''\
        """Module docstring.

        References pattern: forbidden_marker = "x"
        """
        x = 1
        ''')
    out = strip_python_comments_and_strings(src)
    assert "forbidden_marker" not in out, (
        "triple-quoted-string content must be blanked"
    )
    assert "Module docstring" not in out
    # Code after the docstring must remain
    assert "x = 1" in out
    # Line count preserved
    assert out.count("\n") == src.count("\n")


def test_strip_handles_both_triple_quote_styles():
    src = '''
"""double"""
'''
    src += "'''single'''\n"
    out = strip_python_comments_and_strings(src)
    assert "double" not in out
    assert "single" not in out


def test_strip_preserves_line_numbers_for_failure_reporting():
    """The strip's contract: replaces with whitespace placeholders so a
    regex match's ``span()`` line number matches the original source's
    line number. Antibody failure messages depend on this for the
    "look at line N" UX."""
    src = '''line1 = 1
"""
line3 docstring content
line4 docstring content
"""
line6 = 2
'''
    out = strip_python_comments_and_strings(src)
    # Both code lines preserved at their original line numbers
    lines = out.splitlines()
    assert lines[0].startswith("line1 = 1")
    assert lines[5].startswith("line6 = 2")
    # Docstring-replaced lines exist (blank/whitespace) but line count holds
    assert len(lines) == 6


def test_find_forbidden_assignments_ignores_docstring_match(tmp_path):
    """Reproduces the recurring footgun: an antibody describes the
    pattern it bans in its own docstring. Without the strip, the
    antibody false-positives on its own description."""
    src = '''\
"""Module docs.

This file MUST NOT contain `forbidden_pattern = "bad"` at module scope.
Pre-helper, an antibody's regex would match this docstring sentence
and incorrectly fail.
"""
x = 1
y = 2
'''
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(
        fixture, r'forbidden_pattern\s*=\s*"bad"',
    )
    assert matches == [], (
        f"helper false-positived on docstring reference: {matches!r}. "
        f"The antibody's own description should not match."
    )


def test_find_forbidden_assignments_catches_real_code_match(tmp_path):
    """Inverse of the previous test: the helper must NOT silently
    swallow a real regression. If the same pattern appears in code,
    the antibody must flag it."""
    src = textwrap.dedent('''\
        """Module docs."""
        forbidden_pattern = "bad"
        x = 1
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(
        fixture, r'forbidden_pattern\s*=\s*"bad"',
    )
    assert len(matches) == 1, (
        f"helper missed real code match: {matches!r}"
    )


def test_find_forbidden_assignments_catches_match_when_only_pattern_ban_doc_present(tmp_path):
    """End-to-end: a file with BOTH a docstring describing the ban AND
    a real-code violation. Helper must blank the doc but flag the code."""
    src = textwrap.dedent('''\
        """Module docs.

        Banned: ``forbidden = "bad"`` at module scope.
        """
        forbidden = "bad"
        x = 1
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(fixture, r'forbidden\s*=\s*"bad"')
    assert len(matches) == 1, (
        f"helper must catch the code line and ignore the docstring; "
        f"got {matches!r}"
    )


def test_find_all_in_code_returns_match_objects_with_spans(tmp_path):
    """``find_all_in_code`` returns full Match objects so failure
    messages can cite line/column. Pin the contract."""
    src = textwrap.dedent('''\
        x = 1
        BAD_NAME = "violation"
        y = 2
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = list(find_all_in_code(fixture, r"BAD_NAME"))
    assert len(matches) == 1
    span = matches[0].span()
    # The match offset must be where BAD_NAME is in the original source
    assert src[span[0]:span[1]] == "BAD_NAME"


def test_strip_does_not_choke_on_unterminated_triple_quote(tmp_path):
    """Defensive: a pathologically-bad source file shouldn't infinite-loop
    or crash the helper. The strip walks to EOF and stops."""
    src = '"""never closed\nstuff\nmore stuff'
    out = strip_python_comments_and_strings(src)
    # Ran to completion without raising. Output length matches input.
    assert len(out) == len(src)


def test_strip_blanks_triple_quoted_used_as_non_docstring():
    """Limitation acknowledgment: triple-quoted strings used as data
    (e.g., embedded SQL) ARE blanked. Antibodies that need to inspect
    such content should put the data on a single-quoted line or use
    a different test technique."""
    src = textwrap.dedent('''\
        SQL = """
        SELECT * FROM secret_table;
        """
        x = 1
    ''')
    out = strip_python_comments_and_strings(src)
    # The triple-quoted block is blanked even though it's not a docstring
    assert "SELECT" not in out
    assert "secret_table" not in out
    # Code outside the triple-quoted survives
    assert "SQL =" in out
    assert "x = 1" in out
