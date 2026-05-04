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


# ---------------------------------------------------------------------------
# header_only=True (module-level scope)
# ---------------------------------------------------------------------------


def test_header_only_excludes_function_body_match(tmp_path):
    """Pattern that matches inside a function body but NOT at module
    scope must return empty when ``header_only=True``."""
    src = textwrap.dedent('''\
        # module-level imports only
        import os

        def helper():
            CANONICAL = {"a", "b"}
            return CANONICAL
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    # Without header_only: pattern matches the function body
    bare = find_forbidden_assignments(
        fixture, r'CANONICAL\s*=\s*\{', header_only=False,
    )
    assert len(bare) == 1, (
        f"baseline must match function-body assignment: {bare!r}"
    )
    # With header_only: nothing before the first def
    scoped = find_forbidden_assignments(
        fixture, r'CANONICAL\s*=\s*\{', header_only=True,
    )
    assert scoped == [], (
        f"header_only=True must skip function bodies; got {scoped!r}"
    )


def test_header_only_catches_module_level_assignment(tmp_path):
    """Inverse: a hardcoded set at module scope MUST trip the antibody
    even with header_only=True (that's the whole point)."""
    src = textwrap.dedent('''\
        CANONICAL = {"a", "b"}

        def helper():
            return None
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(
        fixture, r'CANONICAL\s*=\s*\{', header_only=True,
    )
    assert len(matches) == 1, (
        f"header_only=True must still catch module-level assignment: "
        f"{matches!r}"
    )


def test_header_only_truncates_at_first_class(tmp_path):
    """Cutoff line is the first def OR class at column 0."""
    src = textwrap.dedent('''\
        TOP = 1

        class Foo:
            BAD = {"x"}
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(
        fixture, r'BAD\s*=\s*\{', header_only=True,
    )
    assert matches == [], (
        f"class-body assignment must be excluded; got {matches!r}"
    )


def test_header_only_truncates_at_first_async_def(tmp_path):
    """``async def`` at column 0 is also a cutoff anchor."""
    src = textwrap.dedent('''\
        TOP = 1

        async def fetch():
            BAD = {"x"}
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(
        fixture, r'BAD\s*=\s*\{', header_only=True,
    )
    assert matches == [], (
        f"async-def-body assignment must be excluded; got {matches!r}"
    )


def test_header_only_returns_whole_src_when_no_def_or_class(tmp_path):
    """Pure-data file (no def/class) is treated as all-header."""
    src = textwrap.dedent('''\
        CONFIG = {"a": 1}
        OTHER = "value"
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(
        fixture, r'CONFIG\s*=', header_only=True,
    )
    assert len(matches) == 1, (
        f"file with no def/class should treat whole src as header; "
        f"got {matches!r}"
    )


def test_header_only_excludes_decorator_kwargs_from_header(tmp_path):
    """Copilot PR #60 review pin: a decorator's kwargs (e.g.
    ``@register(allowed={...})``) belong to the function definition
    that follows, not to the module header. Without this guarantee,
    antibodies that ban hardcoded sets/lists at module scope would
    false-positive on legitimate decorator configuration."""
    src = textwrap.dedent('''\
        TOP = 1

        @register(allowed={"settlement_capture", "center_buy"})
        def foo():
            pass
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(
        fixture, r'allowed\s*=\s*\{', header_only=True,
    )
    assert matches == [], (
        f"decorator kwargs must be excluded from module-header scope; "
        f"got {matches!r}"
    )


def test_header_only_function_body_still_excluded_with_decorator(tmp_path):
    """Companion to the decorator-kwargs test: the body of a decorated
    function is still excluded. The cutoff is the decorator line itself,
    so everything from ``@`` onward is out of scope."""
    src = textwrap.dedent('''\
        TOP = 1

        @decorator
        def foo():
            BAD = {"x"}
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(
        fixture, r'BAD\s*=\s*\{', header_only=True,
    )
    assert matches == [], f"function body must be excluded; got {matches!r}"


def test_find_all_in_code_header_only_excludes_function_body_match(tmp_path):
    """Copilot PR #60 review pin: ``find_all_in_code(header_only=True)``
    must mirror ``find_forbidden_assignments``'s scoping. Pinned
    independently so a regression in one helper's wiring does not
    silently break the line/column-reporting variant."""
    src = textwrap.dedent('''\
        TOP_LEVEL_BAD = {"a"}

        def helper():
            INSIDE_BAD = {"b"}
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    # Without scoping: both matches surface.
    bare = list(find_all_in_code(fixture, r"\w+_BAD\s*=\s*\{", header_only=False))
    assert len(bare) == 2, (
        f"baseline must catch both; got {[m.group() for m in bare]!r}"
    )
    # With scoping: only the module-level match.
    scoped = list(find_all_in_code(fixture, r"\w+_BAD\s*=\s*\{", header_only=True))
    assert len(scoped) == 1, (
        f"header_only=True must drop function-body match; "
        f"got {[m.group() for m in scoped]!r}"
    )
    assert "TOP_LEVEL_BAD" in scoped[0].group()


def test_header_only_still_strips_docstring_in_header(tmp_path):
    """Triple-quoted module docstring is still blanked, even within
    the header — strip happens BEFORE truncation."""
    src = textwrap.dedent('''\
        """Module docs.

        References ``BAD = "x"`` in narrative form.
        """
        OK = 1

        def helper():
            return None
    ''')
    fixture = tmp_path / "subject.py"
    fixture.write_text(src)
    matches = find_forbidden_assignments(
        fixture, r'BAD\s*=\s*"x"', header_only=True,
    )
    assert matches == [], (
        f"docstring-in-header reference must be blanked; got {matches!r}"
    )


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
