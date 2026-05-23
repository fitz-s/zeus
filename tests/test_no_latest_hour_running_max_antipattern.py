# Created: 2026-05-23
# Last reused/audited: 2026-05-23
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-C;
#   src/data/day0_observation_reader.py module docstring (Root C fix).
"""Repo-static guard: no production reader uses the latest-hour running_max anti-pattern.

Anti-pattern definition (Root C bug, 2026-05-22):
    A SQL string that combines ALL of:
      - ORDER BY ... DESC
      - LIMIT 1
      - reading ``running_max`` or ``running_min`` as-is (i.e. NOT via MAX/MIN aggregation)
    This pattern fetches only the *latest* row and reads its per-hour bucket value as the
    day-so-far high/low — wrong whenever the daily peak occurred earlier in the day.

The correct pattern (day0_observation_reader.py Root C fix):
    MAX(running_max) / MIN(running_min) over ALL qualifying rows for the city/date.

Allowlist:
    src/data/day0_observation_reader.py — the defensive helper that was written
        specifically to correct this bug.  It is allowed to MENTION the anti-pattern
        in comments and is required to use MAX(running_max).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

# Repository src root (relative to this test file's location).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"

# The one file that is explicitly allowed to reference the anti-pattern pattern
# (it guards against it and uses MAX aggregation correctly).
_ALLOWLIST = frozenset({"src/data/day0_observation_reader.py"})

# Regex components for the anti-pattern detection.
# We check within individual Python string literals (SQL strings) to avoid
# false positives from a file that has ORDER BY DESC LIMIT 1 for one query
# and a separate mention of running_max in a different context.
_RE_ORDER_BY_DESC = re.compile(r"ORDER\s+BY\b", re.IGNORECASE)
_RE_DESC_LIMIT1 = re.compile(r"\bDESC\b.*\bLIMIT\s+1\b", re.IGNORECASE | re.DOTALL)
_RE_RUNNING_MAX_BARE = re.compile(r"\brunning_max\b", re.IGNORECASE)
_RE_RUNNING_MIN_BARE = re.compile(r"\brunning_min\b", re.IGNORECASE)
# Safe aggregated form: MAX(running_max) or MIN(running_min) — not the anti-pattern.
_RE_MAX_RUNNING_MAX = re.compile(r"\bMAX\s*\(\s*running_max\s*\)", re.IGNORECASE)
_RE_MIN_RUNNING_MIN = re.compile(r"\bMIN\s*\(\s*running_min\s*\)", re.IGNORECASE)


def _extract_string_literals(source: str) -> list[str]:
    """Return all string literal values found in *source* via the AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
    return literals


def _string_has_antipattern(sql: str) -> bool:
    """Return True if *sql* contains the latest-hour running_max anti-pattern.

    A string is flagged when it satisfies ALL of:
      1. Looks like a SQL query (contains SELECT)
      2. Contains ORDER BY ... DESC ... LIMIT 1
      3. Mentions running_max or running_min (bare, i.e. not only via MAX/MIN aggregation)
    """
    if not re.search(r"\bSELECT\b", sql, re.IGNORECASE):
        return False
    if not (_RE_ORDER_BY_DESC.search(sql) and _RE_DESC_LIMIT1.search(sql)):
        return False
    # Check for bare running_max/running_min that is NOT covered by an aggregation.
    has_bare_max = bool(_RE_RUNNING_MAX_BARE.search(sql))
    has_bare_min = bool(_RE_RUNNING_MIN_BARE.search(sql))
    if not (has_bare_max or has_bare_min):
        return False
    # If EVERY occurrence of running_max/running_min is within a MAX()/MIN() call,
    # it is the safe aggregated pattern — not a violation.
    # Strategy: strip all MAX(running_max) / MIN(running_min) substrings and check
    # whether any bare occurrence remains.
    stripped = _RE_MAX_RUNNING_MAX.sub("MAX_RUNNING_MAX_SAFE", sql)
    stripped = _RE_MIN_RUNNING_MIN.sub("MIN_RUNNING_MIN_SAFE", stripped)
    return bool(
        _RE_RUNNING_MAX_BARE.search(stripped) or _RE_RUNNING_MIN_BARE.search(stripped)
    )


def _collect_violations() -> list[tuple[Path, str]]:
    """Return (file_path, excerpt) pairs for every violation found in src/."""
    violations: list[tuple[Path, str]] = []
    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        rel = py_file.relative_to(_REPO_ROOT)
        rel_str = str(rel)
        if rel_str in _ALLOWLIST:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for literal in _extract_string_literals(source):
            if _string_has_antipattern(literal):
                # Truncate long strings for readable error output.
                excerpt = literal[:200].replace("\n", " ")
                violations.append((py_file, excerpt))
    return violations


class TestNoLatestHourRunningMaxAntipattern:
    """Fail if any production SQL string uses ORDER BY DESC LIMIT 1 + bare running_max."""

    def test_no_antipattern_in_src(self):
        violations = _collect_violations()
        assert violations == [], (
            "Anti-pattern detected: ORDER BY DESC LIMIT 1 combined with bare running_max/"
            "running_min (latest-hour-erases-peak bug, Root C).\n"
            + "\n".join(f"  {p}: {exc!r}" for p, exc in violations)
        )

    def test_allowlisted_file_uses_max_aggregation(self):
        """day0_observation_reader.py must use MAX(running_max) in SQL — not bare running_max."""
        reader = _REPO_ROOT / "src" / "data" / "day0_observation_reader.py"
        assert reader.exists(), f"Allowlisted file missing: {reader}"
        source = reader.read_text(encoding="utf-8")
        # Only examine SQL query strings (contain SELECT ... FROM — actual queries).
        # Prose docstrings also contain ORDER BY / running_max for explanatory
        # purposes; requiring SELECT ensures we skip them.
        _RE_SQL_HINT = re.compile(r"\bSELECT\b", re.IGNORECASE)
        found_sql_with_running_max = False
        for literal in _extract_string_literals(source):
            if not _RE_SQL_HINT.search(literal):
                continue
            if not _RE_RUNNING_MAX_BARE.search(literal):
                continue
            found_sql_with_running_max = True
            # Every SQL mention of running_max must be inside MAX(running_max).
            stripped = _RE_MAX_RUNNING_MAX.sub("MAX_RUNNING_MAX_SAFE", literal)
            assert not _RE_RUNNING_MAX_BARE.search(stripped), (
                "day0_observation_reader.py has a SQL string with bare running_max "
                "not wrapped in MAX(): this violates the Root C fix invariant.\n"
                f"  String excerpt: {literal[:200]!r}"
            )
        assert found_sql_with_running_max, (
            "day0_observation_reader.py contains no SQL string using running_max — "
            "the allowlist invariant cannot be verified (file may have changed)."
        )
