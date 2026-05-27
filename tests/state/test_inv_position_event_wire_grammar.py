# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Part-3 audit Finding 1 (PR #352) — one wire vocabulary for
#   position_events.event_type. The canonical enum, the DB CHECK, and the
#   runtime builders must agree byte-for-byte.
"""Relationship invariant: position-event wire grammar is single-sourced.

Finding 1 (Part-3 audit): `CanonicalPositionEventKind` shipped as an unused
aspirational enum with lowercase values and a member set that did NOT match the
uppercase `position_events.event_type` CHECK that runtime actually writes. A
future producer using `CanonicalPositionEventKind.X.value` would emit an
unprojectable row.

This test makes the divergence category impossible:

  1. enum values == the parsed position_events.event_type CHECK set (single
     source of truth — neither may drift from the other).
  2. every string literal assigned to event_type by the canonical builders in
     src/engine/lifecycle_events.py is a member of the enum (no builder may
     invent a wire string outside the grammar).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from src.contracts.position_truth import CanonicalPositionEventKind

ROOT = Path(__file__).resolve().parents[2]
DB_PY = ROOT / "src" / "state" / "db.py"
LIFECYCLE_PY = ROOT / "src" / "engine" / "lifecycle_events.py"


def _parse_position_events_check(db_src: str) -> set[str]:
    """Extract the event_type IN (...) value set from the position_events DDL."""
    # Anchor on the position_events CREATE TABLE, then the first event_type CHECK.
    create = re.search(
        r"CREATE TABLE IF NOT EXISTS position_events\b.*?\n\);",
        db_src,
        re.DOTALL,
    )
    assert create, "position_events CREATE TABLE not found in db.py"
    block = create.group(0)
    m = re.search(r"event_type\s+TEXT\s+NOT NULL\s+CHECK\s*\(event_type\s+IN\s*\(([^)]+)\)", block)
    assert m, "position_events.event_type CHECK IN(...) not found"
    return {tok.strip().strip("'\"") for tok in m.group(1).split(",") if tok.strip()}


def _emitted_event_type_literals(lifecycle_src: str) -> set[str]:
    """AST-collect every string literal bound to event_type in lifecycle_events.py.

    Catches both dict form  {"event_type": "X"}  and keyword form  event_type="X".
    Ignores variable bindings (e.g. event_type=event_type in the _entry_event
    helper) — those resolve to literals at the call sites we DO capture.
    """
    tree = ast.parse(lifecycle_src)
    literals: set[str] = set()
    for node in ast.walk(tree):
        # keyword form: f(..., event_type="X")
        if isinstance(node, ast.keyword) and node.arg == "event_type":
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                literals.add(node.value.value)
        # dict form: {"event_type": "X"}
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "event_type"
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                ):
                    literals.add(v.value)
    return literals


def test_enum_values_equal_position_events_check_set() -> None:
    check_set = _parse_position_events_check(DB_PY.read_text())
    enum_set = {k.value for k in CanonicalPositionEventKind}
    assert enum_set == check_set, (
        "CanonicalPositionEventKind must be the single wire vocabulary for "
        f"position_events.event_type.\n  in enum not in CHECK: {sorted(enum_set - check_set)}\n"
        f"  in CHECK not in enum: {sorted(check_set - enum_set)}"
    )


def test_enum_values_are_uppercase_wire_strings() -> None:
    for k in CanonicalPositionEventKind:
        assert k.value == k.value.upper(), f"enum value {k.value!r} is not the uppercase wire form"


def test_every_emitted_event_type_literal_is_in_enum() -> None:
    emitted = _emitted_event_type_literals(LIFECYCLE_PY.read_text())
    enum_set = {k.value for k in CanonicalPositionEventKind}
    unknown = emitted - enum_set
    assert not unknown, (
        "lifecycle_events.py emits event_type literals outside the canonical "
        f"grammar: {sorted(unknown)}. Add them to CanonicalPositionEventKind "
        "AND the position_events CHECK, or route through the enum."
    )
