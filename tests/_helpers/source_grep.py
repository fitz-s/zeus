# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: oracle/Kelly evidence rebuild context capsule §insight-2
#                  + PR #56 H2 + PR #56 review fix (recurring "regex matched
#                    my own commit message about why we banned this" footgun).
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

Migration note for existing antibodies
--------------------------------------
``test_authority_rebuild_invariants.py`` has two source-grep
antibodies (``test_H2_cycle_runtime_no_hardcoded_strategy_string_literals``,
``test_PR56_evaluator_reads_phase_source_from_candidate_not_hardcode``)
that today implement bespoke "exclude docstring lines" tightening.
A follow-up commit on PR #56's branch can migrate them to use this
helper after PR #56 merges; cross-PR refactors are out of scope here.
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


def find_forbidden_assignments(
    source_file: Path,
    forbidden_pattern: str,
    *,
    flags: int = re.MULTILINE,
) -> list[str]:
    """Search ``source_file`` for ``forbidden_pattern`` in CODE only.

    Comments and string literals (including docstrings) are stripped
    before the regex runs. Returns the list of matched substrings
    (empty = clean).

    Use in antibody tests that need to forbid a specific pattern
    appearing in module-level or function-body code, without
    false-positives on the test's own commentary describing the ban::

        from tests._helpers.source_grep import find_forbidden_assignments

        matches = find_forbidden_assignments(
            REPO_ROOT / "src/foo.py",
            r'^\\s+CANONICAL_KEYS\\s*=\\s*\\{',
        )
        assert not matches, (
            f"Re-introduced hardcoded CANONICAL_KEYS: {matches!r}"
        )

    Pre-helper, the same antibody required an inline ``^\\s+`` anchor
    to dodge its own docstring's reference to the pattern — bespoke
    per call site.
    """
    src = source_file.read_text()
    stripped = strip_python_comments_and_strings(src)
    return re.compile(forbidden_pattern, flags).findall(stripped)


def find_all_in_code(
    source_file: Path,
    pattern: str,
    *,
    flags: int = re.MULTILINE,
) -> Iterable[re.Match]:
    """Iterate over ``re.Match`` objects for ``pattern`` in code (post-strip).

    Variant of ``find_forbidden_assignments`` that returns full match
    objects (with ``.span()`` for line/column reporting) instead of
    just matched strings. Use when failure messages need to cite line
    numbers — the strip preserves offsets.
    """
    src = source_file.read_text()
    stripped = strip_python_comments_and_strings(src)
    return re.compile(pattern, flags).finditer(stripped)
