# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: AST guard — no computed_at.date() (UTC) in lead_days computation; enforces city-local ZoneInfo date at the AST level (BLOCKER 6 structural antibody).
# Reuse: Run with pytest; update if lead_days computation pattern in src/main.py or U0R capture changes.
from __future__ import annotations

# Created: 2026-06-08
# Last reused/audited: 2026-06-08
# Authority basis: PR#400 review item A (operator: "add ast test to prevent
#   computed_at.date()"). B6 bug = U0R lead_days computed from the UTC
#   ``computed_at.date()`` calendar instead of the city-local calendar
#   (ZoneInfo(tz_name).date()), off-by-one across timezones.
"""B6 antibody: the U0R lead-date must be derived from the CITY-LOCAL calendar,
never the raw UTC ``computed_at.date()`` calendar.

``computed_at`` is a UTC instant. The decision date that drives the lead bucket /
regional eligibility / settlement sigma is the *city-local* date of that instant
(``computed_at.astimezone(ZoneInfo(tz_name)).date()``). Taking ``computed_at.date()``
directly reads the UTC calendar and is off-by-one for cities whose local date differs
from UTC at the cutover instant (e.g. Tokyo 2026-06-03T16:30Z is local 06-04, so a
06-04 target is lead 0, not 1). That was the B6 live-money bug.

STRUCTURAL ANTIBODY (makes the category unconstructable, not just the instance):
this guard parses the AST of the U0R lead/date code and FAILS if it finds
``computed_at.date()`` — a ``Call`` on an ``Attribute`` named ``date`` whose value is
the ``Name`` ``computed_at`` — anywhere OTHER THAN inside an ``except`` handler.

The ``except``-handler carve-out is deliberate: the post-fix code keeps a single
defensive ``computed_at.date()`` fallback that only runs when ``ZoneInfo(tz_name)``
is unresolvable (the primary path is the city-local conversion). That fallback is NOT
the bug. Reverting the PRIMARY (non-``except``) path back to ``computed_at.date()`` —
the actual B6 regression — turns this test RED.

GREEN now because the fix uses ``computed_at.astimezone(ZoneInfo(tz_name)).date()`` on
the primary path: there the ``.date()`` Attribute's value is the ``astimezone(...)``
Call, not the ``computed_at`` Name, so it does not match.
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


# Files that compute U0R lead / decision dates from ``computed_at``. The B6 fix and
# its only legitimate ``computed_at.date()`` fallback both live in the materializer;
# any future module that derives a U0R lead from ``computed_at`` must be added here.
U0R_LEAD_DATE_PATHS = (
    "src/data/replacement_forecast_materializer.py",
)


def _is_computed_at_date_call(node: ast.AST) -> bool:
    """True iff ``node`` is the expression ``computed_at.date()``.

    Matches a ``Call`` whose ``func`` is an ``Attribute`` ``date`` whose ``value`` is
    the bare ``Name`` ``computed_at`` and which takes no arguments. Deliberately does
    NOT match ``computed_at.astimezone(...).date()`` (the Attribute's value there is a
    ``Call``, not the ``Name`` ``computed_at``) — that is the correct city-local form.
    """
    if not isinstance(node, ast.Call):
        return False
    if node.args or node.keywords:
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "date":
        return False
    value = func.value
    return isinstance(value, ast.Name) and value.id == "computed_at"


def _nodes_inside_except_handlers(tree: ast.AST) -> set[int]:
    """Return ``id()`` of every AST node that lives inside any ``except`` handler body.

    A ``computed_at.date()`` inside an ``except`` block is the permitted defensive
    fallback (ZoneInfo unresolvable); everywhere else it is the B6 regression.
    """
    excused: set[int] = set()
    for handler in ast.walk(tree):
        if isinstance(handler, ast.ExceptHandler):
            for body_node in handler.body:
                for child in ast.walk(body_node):
                    excused.add(id(child))
    return excused


def _utc_lead_findings(relative_path: str) -> list[str]:
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    excused = _nodes_inside_except_handlers(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if _is_computed_at_date_call(node) and id(node) not in excused:
            findings.append(f"{relative_path}:{node.lineno}: {ast.unparse(node)}")
    return findings


def _utc_lead_snippet_findings(source: str) -> list[str]:
    tree = ast.parse(source)
    excused = _nodes_inside_except_handlers(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if _is_computed_at_date_call(node) and id(node) not in excused:
            findings.append(f"<snippet>:{node.lineno}: {ast.unparse(node)}")
    return findings


def test_u0r_lead_code_does_not_use_utc_computed_at_date():
    """No primary (non-except) ``computed_at.date()`` in the U0R lead/date code.

    GREEN with the B6 fix in place (city-local ``astimezone(ZoneInfo(tz_name)).date()``
    primary; UTC ``computed_at.date()`` only inside the ``except`` fallback). RED the
    instant someone reverts the primary lead computation to the UTC calendar.
    """
    findings: list[str] = []
    for relative_path in U0R_LEAD_DATE_PATHS:
        findings.extend(_utc_lead_findings(relative_path))

    assert not findings, (
        "B6 regression: U0R lead/date computed from the UTC `computed_at.date()` "
        "calendar outside an except fallback. Use the city-local "
        "`computed_at.astimezone(ZoneInfo(tz_name)).date()` instead:\n"
        + "\n".join(findings)
    )


def test_guard_flags_a_primary_utc_computed_at_date_revert():
    """The guard goes RED on the exact B6 revert (primary UTC `computed_at.date()`)."""
    reverted = """
def lead(computed_at, target_local_date):
    computed_local_date = computed_at.date()
    return max(0, (target_local_date - computed_local_date).days)
"""
    findings = _utc_lead_snippet_findings(reverted)
    assert len(findings) == 1, "\n".join(findings)


def test_guard_allows_city_local_primary_with_except_fallback():
    """The guard stays GREEN on the post-fix shape: city-local primary + except UTC fallback."""
    fixed = """
def lead(computed_at, target_local_date, tz_name):
    try:
        from zoneinfo import ZoneInfo
        computed_local_date = computed_at.astimezone(ZoneInfo(tz_name)).date()
    except Exception:
        computed_local_date = computed_at.date()
    return max(0, (target_local_date - computed_local_date).days)
"""
    findings = _utc_lead_snippet_findings(fixed)
    assert findings == [], "\n".join(findings)
