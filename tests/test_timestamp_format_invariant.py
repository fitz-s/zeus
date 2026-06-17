# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md
#   Part V §ANTIBODY 2 / C3 — timestamp-format corruption prevention.
"""CI antibody: no `DEFAULT CURRENT_TIMESTAMP` or naive `strftime` default on any
column whose name matches a timing pattern.

WHY THIS EXISTS
---------------
Defect C3: SQLite `DEFAULT CURRENT_TIMESTAMP` writes the host's local clock string
with no timezone marker (e.g. `2026-06-15 10:23:45`).  On the Chicago host (CDT =
UTC-5) this is 5 h off UTC and mixes a `' '`-separated format with the ISO `'T'`-
separated format used elsewhere, corrupting string-comparison sort order.

31 of 115 audited timestamp write sites carry a NAIVE_CURRENT_TS basis (verified
live: `observation_revisions.recorded_at` 134,250 rows naive; `readiness_state`,
`market_topology_state`, `venue_order_facts`, `source_run_coverage` all corrupt).

The fix is: caller-supplied tz-aware ISO via `utc_iso_now()` (src/state/db.py).
This test BLOCKS any new `DEFAULT CURRENT_TIMESTAMP` or naive `strftime` default
from entering a timing column.

WHAT FAILS
----------
Any `CREATE TABLE` or `ALTER TABLE ADD COLUMN` DDL string found in:
  src/state/db.py
  src/state/schema/*.py
that contains a column matching the timing-name pattern AND whose DEFAULT is
`CURRENT_TIMESTAMP` or a naive `strftime(...)`.

TIMING-COLUMN PATTERN
---------------------
Any column name containing one of:
  _at  |  _time  |  timestamp  |  recorded  |  ingested  |  updated  |  expires

WHAT IS ALLOWED
---------------
`DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))` — SQLite's `'now'` modifier
returns UTC; `+00:00` makes the offset explicit in the string.  The C3 fix agent
converted all `CURRENT_TIMESTAMP` sites to this form, which IS tz-safe.

`DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))` with capital `%S` (seconds only,
no sub-seconds) and `Z` suffix is also safe since `'now'` in SQLite is UTC.

WHAT IS FORBIDDEN
-----------------
* `DEFAULT CURRENT_TIMESTAMP` — no tz marker, local clock on many platforms.
* `DEFAULT (strftime(..., 'now'))` where the format string does NOT include a
  UTC marker (`+00:00`, `+00`, or terminal `Z`).  For example:
  `strftime('%Y-%m-%d %H:%M:%S', 'now')` — space-separated naive format.

Columns that carry `DEFAULT NULL` or no default are fine.
Caller-supplied tz-aware ISO is always preferred over any DDL default.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# Files whose DDL strings are scanned.
DDL_FILES = [
    SRC_ROOT / "state" / "db.py",
    *sorted((SRC_ROOT / "state" / "schema").glob("*.py")),
]

# Column-name substrings that mark a timing column.
TIMING_COLUMN_PATTERNS = re.compile(
    r"_at\b|_time\b|timestamp|recorded|ingested|updated|expires",
    re.IGNORECASE,
)

# Forbidden DEFAULT expressions (case-insensitive).
# Forbids:
#   DEFAULT CURRENT_TIMESTAMP                          — no tz, local clock
#   DEFAULT (strftime('... no utc offset ...', 'now')) — naive format
#
# Allows:
#   DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))  — UTC-explicit via +00:00
#   DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))        — UTC via Z suffix
#   DEFAULT NULL / no DEFAULT                               — always safe
#
# Implementation: match lines that have CURRENT_TIMESTAMP alone, OR strftime('...', 'now')
# where the format string contains NEITHER '+00' NOR a terminal 'Z' before the closing quote.
_FORBIDDEN_STRFTIME_RE = re.compile(
    r"strftime\s*\(\s*'([^']*)'\s*,\s*'now'",
    re.IGNORECASE,
)


def _is_naive_strftime(line: str) -> bool:
    """True if the line has a strftime('...', 'now') where the format lacks a UTC marker."""
    for m in _FORBIDDEN_STRFTIME_RE.finditer(line):
        fmt = m.group(1)
        # Safe if the format string includes +00 or ends with Z before the closing quote
        if "+00" in fmt or fmt.rstrip().endswith("Z"):
            continue  # tz-aware form — ok
        return True
    return False


CURRENT_TIMESTAMP_RE = re.compile(r"\bDEFAULT\s+CURRENT_TIMESTAMP\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def _is_forbidden_default(line: str) -> bool:
    """True when the line contains a forbidden (naive/host-local) DEFAULT."""
    if CURRENT_TIMESTAMP_RE.search(line):
        return True
    if _is_naive_strftime(line):
        return True
    return False


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, violation_description) for the given file."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []

    violations: list[tuple[int, str]] = []
    lines = source.splitlines()

    for i, line in enumerate(lines, start=1):
        # Skip comment-only lines.
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # Check for forbidden DEFAULT patterns on this line.
        if not _is_forbidden_default(line):
            continue

        # This line has a forbidden DEFAULT; now check if it is a timing column.
        # Look for a column name on the same line.
        col_match = re.search(
            r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+\w+\s+.*DEFAULT",
            line,
            re.IGNORECASE,
        )
        col_name = col_match.group(1) if col_match else None

        if col_name and TIMING_COLUMN_PATTERNS.search(col_name):
            violations.append(
                (
                    i,
                    f"timing column `{col_name}` has a naive/host-local DEFAULT "
                    f"(CURRENT_TIMESTAMP or naive strftime): {stripped[:100]!r}",
                )
            )
        elif not col_name:
            # Could not extract col name — flag conservatively if any timing word
            # appears on the same line.
            if TIMING_COLUMN_PATTERNS.search(line):
                violations.append(
                    (
                        i,
                        f"possible timing column with naive DEFAULT on line: {stripped[:100]!r}",
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_no_naive_current_timestamp_on_timing_columns() -> None:
    """No DDL in src/state/ may write DEFAULT CURRENT_TIMESTAMP or naive strftime
    on a column whose name matches a timing pattern.

    Failures → replace the DEFAULT with no default and supply a tz-aware ISO value
    from the caller (utc_iso_now() once it exists, or datetime.now(timezone.utc).isoformat()
    until then).

    See: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md
         Part V ANTIBODY 2 / C3 timestamp-format corruption.
    """
    all_violations: list[str] = []

    for ddl_file in DDL_FILES:
        if not ddl_file.exists():
            continue
        relpath = str(ddl_file.relative_to(SRC_ROOT))
        file_violations = _scan_file(ddl_file)
        for lineno, desc in file_violations:
            all_violations.append(f"  src/{relpath}:{lineno}: {desc}")

    if all_violations:
        joined = "\n".join(sorted(all_violations))
        pytest.fail(
            f"\n\n{len(all_violations)} timestamp-format invariant violation(s):\n\n"
            + joined
            + "\n\n"
            + textwrap.dedent(
                """
                FIX: remove `DEFAULT CURRENT_TIMESTAMP` (or naive `strftime`) from
                these timing columns.  Callers must supply a tz-aware ISO string at
                INSERT time via:
                    datetime.now(timezone.utc).isoformat()
                or the canonical helper `utc_iso_now()` once available in src/state/db.py.
                See: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md C3.
                """
            ).strip(),
        )
