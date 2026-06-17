# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md
#   Part V §ANTIBODY 5c — C4 synthetic/dead instrumentation prevention.
"""CI antibody: ban three exact synthetic-instrumentation patterns in src/.

WHY THIS EXISTS
---------------
Defect C4 (TELEMETRY): three concrete synthetic patterns were found in live code:

1. `posted_at = filled_at`
   (src/events/edli_position_bridge.py:574, :978)
   Stamps the fill confirmation time as the submission time — collapses a real
   latency to synthetic zero.  The correct value is the venue-order created_at.

2. `latency_ms = 0` or `latency_seconds = 0` as a literal constant write
   Writes fabricated zero latency.  The honest answer is NULL when unknown.

3. `venue_timestamp = ack_time`
   (src/execution/executor.py:3063, :4146, :4171)
   Stamps the local ACK receipt wall-clock as the venue's match timestamp.
   The correct source is the WebSocket matchtime field from the fill payload.

4. `venue_timestamp = datetime.now(`
   Same error via a direct now() call.

HONEST_NULL_COLUMNS
-------------------
Some columns are intentionally write-NULL (documented in the plan, C4 §Fix):
`execution_feasibility_evidence.latency_ms`,
`execution_feasibility_evidence.submit_time`,
`execution_feasibility_evidence.order_intent_time`.

These are declared in HONEST_NULL_COLUMNS below.  An entry in this set ONLY
exempts the literal-zero check (pattern 2) when the column/keyword name matches
an entry — it does NOT exempt the other three patterns.

ADDING AN EXEMPTION
-------------------
* Pattern 1 (`posted_at = filled_at`): there is no valid exemption; fix the site.
* Pattern 2 (literal zero): add to HONEST_NULL_COLUMNS if the column is documented
  as intentionally write-NULL elsewhere; the zero should then become NULL anyway,
  so this is a transitional measure only.
* Pattern 3/4 (venue_timestamp): there is no valid exemption; fix the site.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# Columns that are intentionally left NULL (not written as zero).
# Format: "table.column"  or just "column" when unambiguous.
# These are seeded from C4 §HONEST_NULL_COLUMNS in the plan.
HONEST_NULL_COLUMNS: frozenset[str] = frozenset(
    {
        "execution_feasibility_evidence.latency_ms",
        "execution_feasibility_evidence.submit_time",
        "execution_feasibility_evidence.order_intent_time",
        # execution_fact.latency_seconds is computed from posted_at/filled_at when
        # available — only honest-zero (command_recovery same-second) is legitimate.
        # Left out of the exemption set intentionally; the literal-0 ban still applies.
    }
)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _unparse(node: ast.expr) -> str:
    return ast.unparse(node) if hasattr(ast, "unparse") else repr(node)


class _SyntheticPatternVisitor(ast.NodeVisitor):
    """Detect synthetic timing patterns.

    Patterns detected:
    1. `posted_at = filled_at` — same-name passthrough collapsing latency.
    2. `latency_ms = 0` or `latency_seconds = 0` as a literal integer/float.
    3. `venue_timestamp = ack_time` — local ACK clock masking as venue match time.
    4. `venue_timestamp = datetime.now(...)` — now() stamped as venue event time.

    Patterns 1/3/4 are detected in both plain assignments and keyword args.
    Pattern 2 is detected in plain assignments and keyword args; exempted when the
    column name is in HONEST_NULL_COLUMNS (transitional: should become NULL not 0).
    """

    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []  # (lineno, description)

    def _check_assignment(self, lineno: int, name: str, value: ast.expr) -> None:
        rhs = _unparse(value)
        lhs = name

        # Pattern 1: posted_at = filled_at
        if lhs == "posted_at" and rhs == "filled_at":
            self.violations.append(
                (lineno, f"`posted_at = filled_at` — synthetic: collapses submission latency to zero. "
                         f"Fix: use venue_commands.created_at (or NULL if unavailable).")
            )

        # Also catch order_posted_at = filled_at (same defect, different column alias)
        if lhs == "order_posted_at" and rhs == "filled_at":
            self.violations.append(
                (lineno, f"`order_posted_at = filled_at` — synthetic: collapses submission latency to zero. "
                         f"Fix: use venue_commands.created_at (or NULL if unavailable).")
            )

        # Pattern 2: latency_ms = 0 / latency_seconds = 0 as literal
        if lhs in ("latency_ms", "latency_seconds"):
            if isinstance(value, ast.Constant) and value.value == 0:
                col_key = f"execution_feasibility_evidence.{lhs}"
                if col_key not in HONEST_NULL_COLUMNS and lhs not in HONEST_NULL_COLUMNS:
                    self.violations.append(
                        (lineno, f"`{lhs} = 0` — synthetic zero latency. "
                                 f"Fix: compute from real posted_at/filled_at, or write NULL. "
                                 f"Add to HONEST_NULL_COLUMNS if intentionally NULL.")
                    )

        # Pattern 3: venue_timestamp = ack_time
        if lhs == "venue_timestamp":
            if isinstance(value, ast.Name) and value.id == "ack_time":
                self.violations.append(
                    (lineno, f"`venue_timestamp = ack_time` — local ACK receipt time stamped as venue "
                             f"match timestamp. Fix: use WS matchtime field; REST path → write NULL.")
                )

        # Pattern 4: venue_timestamp = datetime.now(...)
        if lhs == "venue_timestamp":
            if isinstance(value, ast.Call):
                func = _unparse(value.func)
                if "datetime.now" in func or func == "now":
                    self.violations.append(
                        (lineno, f"`venue_timestamp = datetime.now(...)` — wallclock stamped as venue "
                                 f"event time. Fix: use WS matchtime field; REST path → write NULL.")
                    )

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._check_assignment(node.lineno, target.id, node.value)
            elif isinstance(target, ast.Attribute):
                self._check_assignment(node.lineno, target.attr, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            self.generic_visit(node)
            return
        target = node.target
        if isinstance(target, ast.Name):
            self._check_assignment(node.lineno, target.id, node.value)
        elif isinstance(target, ast.Attribute):
            self._check_assignment(node.lineno, target.attr, node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        for kw in node.keywords:
            if kw.arg is not None:
                self._check_assignment(kw.value.lineno, kw.arg, kw.value)
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_no_synthetic_timing_column_patterns() -> None:
    """No src/ file may write the three banned synthetic-instrumentation patterns:
    (1) posted_at = filled_at  (2) latency_ms/latency_seconds = 0  (3/4) venue_timestamp = ack_time/datetime.now().

    Failures → fix the write site to use the real event time or honest NULL.
    HONEST_NULL_COLUMNS lists columns that are intentionally write-NULL (and must
    therefore become NULL, not zero).

    See: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md
         Part V ANTIBODY 5c / C4 synthetic instrumentation.
    """
    all_violations: list[str] = []

    for path in sorted(SRC_ROOT.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        visitor = _SyntheticPatternVisitor()
        visitor.visit(tree)

        relpath = str(path.relative_to(SRC_ROOT))
        for lineno, desc in visitor.violations:
            all_violations.append(f"  src/{relpath}:{lineno}: {desc}")

    if all_violations:
        joined = "\n".join(sorted(all_violations))
        pytest.fail(
            f"\n\n{len(all_violations)} synthetic timing-column violation(s):\n\n"
            + joined
            + "\n\n"
            + textwrap.dedent(
                """
                FIX OPTIONS:
                  posted_at = filled_at        → posted_at = venue_commands.created_at (NULL if absent)
                  latency_ms = 0               → latency_ms = None  (or compute from real times)
                  latency_seconds = 0          → latency_seconds = None
                  venue_timestamp = ack_time   → venue_timestamp = ws_fill_payload.get("matchtime") or None
                  venue_timestamp = datetime.now() → None (honest absence on REST ACK)

                See: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md C4.
                """
            ).strip(),
        )
