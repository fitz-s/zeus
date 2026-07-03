# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md
#   Part V §ANTIBODY 5d — C6 date.today() ban enforcement.
#   src/contracts/epistemic_context.py:12 — the prose ban this test machine-enforces.
"""CI antibody: `date.today()` is forbidden in src/ (outside excluded offline dirs).

WHY THIS EXISTS
---------------
`src/contracts/epistemic_context.py:12` documents the system-wide ban:
    "Strictly forbids 'date.today()' scattered locally across the system."

The ban exists because `date.today()` reads the host's LOCAL wall-clock date (not
UTC), and on the Chicago host (UTC-5/UTC-6) it can silently return yesterday's date
near UTC midnight, mislabelling data for markets in other timezones.

MONEY-PATH INSTANCE FOUND (C6):
The removed shoulder vNext scaffold previously used `date.today()` to tag
market families — a host-local date near UTC midnight mis-tags a non-US market.

As of 2026-06-15 there were 10 call sites in src/ (see prevention_scaffolding_2026-06-15.md §4).
This test blocks any new ones from entering the codebase.

EXCLUDED DIRECTORIES
--------------------
`src/calibration/` — offline scripts that operate on historical data, not live
cycle time, where the semantics of date.today() do not introduce a live-system
epistemic error.  The exclusion is documented here and must not be widened without
an explicit operator decision.

NOTE ON EXISTING VIOLATIONS
----------------------------
This test will FAIL against the current codebase because 7+ sites have not yet
been fixed.  That is the intended behavior — the test surfaces the remaining work
for the C6-fix agent.  Do NOT weaken this test to pass; fix the sites instead.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

# Directories excluded from the ban (relative to SRC_ROOT, POSIX paths).
# These are offline/historical scripts where date.today() does not introduce
# a live-cycle epistemic error.  Must not be widened without operator sign-off.
EXCLUDED_DIR_NAMES = frozenset(
    {
        "calibration",  # offline calibration scripts; not live-cycle code
    }
)

# The ban pointer — shown in failure messages.
BAN_REFERENCE = "src/contracts/epistemic_context.py:12"


# ---------------------------------------------------------------------------
# AST helper
# ---------------------------------------------------------------------------


class _DateTodayVisitor(ast.NodeVisitor):
    """Find every `date.today()` call in an AST.

    Detects:
      - date.today()         (attribute call on the `date` name)
      - _date.today()        (import alias form used in some files)
      - datetime.date.today()

    Does NOT flag comments or docstrings — AST parsing skips those.
    """

    def __init__(self) -> None:
        self.hits: list[int] = []  # line numbers

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        # date.today() or _date.today() or similar.today()
        if isinstance(func, ast.Attribute) and func.attr == "today":
            callee_src = ast.unparse(func) if hasattr(ast, "unparse") else ""
            # Match date.today / _date.today / datetime.date.today
            if (
                isinstance(func.value, ast.Name)
                and func.value.id in ("date", "_date")
            ) or (
                isinstance(func.value, ast.Attribute)
                and func.value.attr == "date"
            ):
                self.hits.append(node.lineno)
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_no_date_today_in_src() -> None:
    """No file under src/ (except excluded offline dirs) may call date.today().

    The epistemic ban is declared at src/contracts/epistemic_context.py:12.
    Replace every site with `datetime.now(timezone.utc).date()` so the date is
    derived from a UTC-aware clock, not the host's local wall-clock.

    This test WILL FAIL until all C6 fix sites are remediated.  That is correct.

    See: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md C6.
    """
    violations: list[str] = []

    for path in sorted(SRC_ROOT.rglob("*.py")):
        # Check exclusions.
        rel = path.relative_to(SRC_ROOT)
        # rel.parts[0] is the top-level package dir under src/
        if rel.parts and rel.parts[0] in EXCLUDED_DIR_NAMES:
            continue

        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue

        # Fast pre-filter: skip files that don't mention 'today' at all.
        if "today" not in source:
            continue

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        visitor = _DateTodayVisitor()
        visitor.visit(tree)

        relpath = str(rel)
        for lineno in visitor.hits:
            violations.append(f"  src/{relpath}:{lineno}: `date.today()` call")

    if violations:
        joined = "\n".join(sorted(violations))
        pytest.fail(
            f"\n\n{len(violations)} date.today() ban violation(s):\n\n"
            + joined
            + "\n\n"
            + textwrap.dedent(
                f"""
                BAN declared at: {BAN_REFERENCE}
                "Strictly forbids 'date.today()' scattered locally across the system."

                FIX: replace every call with
                    from datetime import datetime, timezone
                    today_utc = datetime.now(timezone.utc).date()

                This gives the correct UTC date regardless of the host's local timezone
                (Chicago host is UTC-5/UTC-6; `date.today()` returns the wrong date
                near UTC midnight for non-US markets).

                Excluded dirs (offline, not live-cycle): {sorted(EXCLUDED_DIR_NAMES)}

                See: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md C6.
                """
            ).strip(),
        )
