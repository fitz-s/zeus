# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: oracle/Kelly evidence rebuild context capsule §insight-2
#                  + PR #56 H2 + PR #56 review fix (recurring "regex matched
#                    my own commit message about why we banned this" footgun).
#                  + post-PR #56/#57 migration: ``header_only`` param added so
#                    antibodies that need "module-level only" scope (e.g. ban a
#                    hardcoded set BEFORE the first def/class) can stop
#                    re-implementing the cutoff loop bespoke per call site.
"""Source-grep helpers for antibody tests.

Antibody tests that grep source for forbidden patterns (e.g.
"no module-level hardcoded strategy frozenset", "no
``_phase_source = \"verified_gamma\"`` literal") repeatedly hit the
same footgun: the regex matches the test's OWN docstring or the
historical-pattern commentary in the source file's comments. The
result: the antibody false-positives on its own description and goes
red without a real regression.

The recurring fix on each call site has been a "tighten the regex"
ratchet — `^\\s+` line-start anchor, exclude `# ...` lines, etc. —
each one bespoke. ``find_forbidden_assignments`` strips comments and
string literals (including triple-quoted docstrings) BEFORE the
regex runs, so antibodies can be expressed in their natural form
without working around their own commentary.

Layout invariants
-----------------
- The strip preserves line numbers and column offsets (replaces with
  whitespace, not removed). Failure messages can still cite source
  positions that match what an editor shows.
- The strip is single-pass and string-state aware. It correctly
  handles triple-quoted strings spanning many lines, escape
  sequences in single-quoted strings, and comments embedded inside
  raw or f-strings.
- No external dependency (lives in the standard test-discoverable
  path so it can be imported as ``from tests._helpers.source_grep
  import find_forbidden_assignments``).

``header_only=True`` (module-level scope)
-----------------------------------------
Some antibodies care only about MODULE-LEVEL declarations — code
appearing BEFORE the first ``def``/``class`` line. Example: forbid a
hardcoded ``CANONICAL_STRATEGY_KEYS = {...}`` constant while allowing
function bodies to legitimately enumerate strategies for dispatch.
Without scoping, the antibody would false-positive on the helper
function that defines the canonical set.

Pre-helper, each call site re-implemented the cutoff::

    lines = src.splitlines()
    cutoff = next(i for i, l in enumerate(lines)
                  if l.startswith(("def ", "class ")))
    header = "\\n".join(lines[:cutoff])

``header_only=True`` does this once, after the strip, so the strip's
line/column preservation still holds for failure reporting.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


def strip_python_comments_and_strings(src: str) -> str:
    """Return ``src`` with Python comments and TRIPLE-quoted strings
    replaced by whitespace placeholders that preserve line/column
    offsets.

    Why only triple-quoted strings (not all string literals)
    --------------------------------------------------------
    The recurring false-positive vector for source-grep antibodies is
    docstrings — and Python docstrings are conventionally triple-quoted.
    Single-line strings (``"..."`` / ``'...'``) overwhelmingly carry
    actual data: dict keys, function arg defaults, regex patterns,
    SQL fragments. Antibodies that search for a SPECIFIC VALUE must
    be able to match such literals (e.g.
    ``_phase_source = "verified_gamma"`` is the literal we want to
    forbid; blanking ``"verified_gamma"`` would defeat the antibody).

    Triple-quoted strings, by contrast, are nearly always docstrings
    or multi-line SQL/HTML — neither of which a typical antibody
    needs to inspect for forbidden patterns. Blanking them removes
    the docstring-self-reference false-positive without breaking
    real-code antibody patterns.

    What gets blanked:
      - ``# ...`` to end of line
      - ``\"\"\"...\"\"\"`` and ``'''...'''`` triple-quoted strings
        (typical docstring vehicle, may span many lines)

    What is preserved verbatim:
      - Code itself (operators, identifiers, keywords)
      - Single-quoted string literals (``"..."`` / ``'...'``)
      - Indentation and newlines (line/col references remain stable)

    Limitations:
      - A triple-quoted string used as a non-docstring (e.g.
        ``SQL = '''SELECT ...'''``) WILL be blanked. Acceptable because
        antibodies that need to inspect SQL should write the SQL on a
        single-quoted line or use a different test technique.
      - Backslash escapes inside single-quoted strings: tracked so a
        ``"foo\\""`` doesn't terminate early; the OUTER literal stays
        verbatim.
      - f-strings are treated as regular strings; the interpolation
        syntax ``f"...{x}..."`` is not parsed.
    """
    out = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "#":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c in ('"', "'"):
            quote = c
            triple = src[i:i + 3]
            if triple == quote * 3:
                # Triple-quoted: blank, may span lines.
                end = src.find(triple, i + 3)
                end = n if end < 0 else end + 3
                for k in range(i, end):
                    out.append("\n" if src[k] == "\n" else " ")
                i = end
            else:
                # Single-line string: PRESERVE verbatim, but track its
                # bounds so a stray `#` inside doesn't get treated as
                # a comment by the outer loop. We append the whole
                # literal and advance past it.
                end = i + 1
                while end < n and src[end] != quote:
                    if src[end] == "\\" and end + 1 < n:
                        end += 2
                    elif src[end] == "\n":
                        break
                    else:
                        end += 1
                end = min(end + 1, n)
                out.append(src[i:end])
                i = end
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _truncate_to_module_header(src: str) -> str:
    """Return ``src`` truncated at the first line that starts with
    ``def ``, ``class ``, or ``async def `` at column 0.

    Used by ``header_only=True`` to scope antibodies to module-level
    declarations only.

    Cutoff anchors (column-0):
      - ``def `` / ``async def `` / ``class `` (the definition itself)
      - ``@`` (decorators preceding a definition) — included so that
        decorator kwargs containing set/list literals (e.g.
        ``@register(allowed_keys={"settlement_capture"})``) cannot
        re-introduce the false-positive that ``header_only`` exists
        to eliminate. Copilot review on PR #60 flagged this gap.

    Decorators on module-level constants (``@cached(...) X = ...``)
    don't exist syntactically in Python, so anchoring at ``@`` cannot
    miscount real module-level constants.

    If no anchor is found, returns ``src`` unchanged (the entire file
    is treated as header — typical of a pure-data config module).
    """
    lines = src.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith(("def ", "class ", "async def ", "@")):
            return "".join(lines[:i])
    return src


def find_forbidden_assignments(
    source_file: Path,
    forbidden_pattern: str,
    *,
    flags: int = re.MULTILINE,
    header_only: bool = False,
) -> list[str]:
    """Search ``source_file`` for ``forbidden_pattern`` in CODE only.

    Comments and triple-quoted strings (the docstring vehicle) are
    stripped before the regex runs. Returns the list of matched
    substrings (empty = clean).

    When ``header_only=True``, the search is further scoped to
    module-level code — everything before the first
    ``def``/``class``/``async def`` line at column 0. Use this when
    forbidding a hardcoded data structure at module scope while
    allowing function bodies to legitimately reference the same
    names (e.g. a helper that returns the canonical set).

    Use in antibody tests that need to forbid a specific pattern
    appearing in module-level or function-body code, without
    false-positives on the test's own commentary describing the ban::

        from tests._helpers.source_grep import find_forbidden_assignments

        matches = find_forbidden_assignments(
            REPO_ROOT / "src/foo.py",
            r'CANONICAL_KEYS\\s*=\\s*\\{',
            header_only=True,
        )
        assert not matches, (
            f"Re-introduced hardcoded CANONICAL_KEYS at module scope: "
            f"{matches!r}"
        )

    Pre-helper, the same antibody required an inline ``^\\s+`` anchor
    to dodge its own docstring's reference to the pattern AND a
    bespoke pre-search splitlines/cutoff loop to enforce module
    scope — both bespoke per call site.
    """
    src = source_file.read_text()
    stripped = strip_python_comments_and_strings(src)
    if header_only:
        stripped = _truncate_to_module_header(stripped)
    return re.compile(forbidden_pattern, flags).findall(stripped)


def find_all_in_code(
    source_file: Path,
    pattern: str,
    *,
    flags: int = re.MULTILINE,
    header_only: bool = False,
) -> Iterable[re.Match]:
    """Iterate over ``re.Match`` objects for ``pattern`` in code (post-strip).

    Variant of ``find_forbidden_assignments`` that returns full match
    objects (with ``.span()`` for line/column reporting) instead of
    just matched strings. Use when failure messages need to cite line
    numbers — the strip preserves offsets.

    ``header_only=True`` scopes the search to module-level code (see
    ``find_forbidden_assignments`` for details).
    """
    src = source_file.read_text()
    stripped = strip_python_comments_and_strings(src)
    if header_only:
        stripped = _truncate_to_module_header(stripped)
    return re.compile(pattern, flags).finditer(stripped)
