# Created: 2026-09-29
# Authority basis: src/contracts/family_fault_scope.py; live cut stalls
#   8df58fddc / 14244e742 / 3d9154568.
"""Structural antibodies for the family-vs-cut prepare fault rule.

Every exception raised inside ``_prepare_current_global_probability_family``
reaches one boundary, ``family_fault_tag``.  A ``ValueError`` excludes only its
family.  These tests keep that true as the call tree grows:

* every raised type that is NOT a ValueError must be a reviewed cut-level type;
  a new family verdict raised as some new exception class would silently stop
  every cut, so it fails here instead;
* a handler that catches a DB/OS fault and raises a ValueError must chain it
  (``from exc`` or implicit context), never ``from None``, so the DB fault stays
  visible to the rule.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from src.contracts.family_fault_scope import (
    FAMILY_AUTHORITY_UNAVAILABLE,
    TRANSIENT_FAMILY_AUTHORITY_UNAVAILABLE,
    GlobalValueFault,
    family_fault_tag,
)

ROOT = Path(__file__).resolve().parents[2]
PREPARE = ("src.engine.event_reactor_adapter", "_prepare_current_global_probability_family")

# Reviewed exception types that stop the whole cut when they escape a family's
# prepare.  Each is shared state, a DB/OS fault, or a code fault.  A family
# evidence verdict must be a ValueError (or subclass) instead.
CUT_LEVEL_TYPES = frozenset(
    {
        "GlobalValueFault",
        "sqlite3.OperationalError",
        "ReplacementInputHwmReadUnavailable",  # sqlite3.OperationalError
        "CurrentValueServingReadUnavailable",  # sqlite3.OperationalError
        "TimeoutError",
        "RuntimeError",
        "SettlementSigmaFloorError",  # global calibration artifact
        "EmosMuOffsetError",  # global calibration artifact
        "TypeError",
        "KeyError",
        "AssertionError",
    }
)
# ValueError subclasses defined outside builtins; family scoped by inheritance.
VALUE_ERROR_SUBCLASSES = frozenset(
    {
        "ValueError",
        "KmaObservationConflict",
        "ResolutionError",
        "Day0AuthorityError",
        "ContractViolation",
        "FamilyKeyingError",
        "BinTopologyError",
        "UnregisteredRawForecastArtifactIdentityError",
    }
)
DB_OR_OS = frozenset(
    {"sqlite3.Error", "sqlite3.OperationalError", "sqlite3.DatabaseError", "OSError"}
)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _index():
    defs: dict[tuple[str, str], ast.AST] = {}
    imports: dict[str, dict[str, str]] = {}
    classes: dict[str, set[str]] = {}
    for path in (ROOT / "src").rglob("*.py"):
        mod = _module_name(path)
        tree = ast.parse(path.read_text())
        imp = imports.setdefault(mod, {})
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imp[alias.asname or alias.name] = f"{node.module}:{alias.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imp[alias.asname or alias.name.split(".")[0]] = alias.name
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs[(mod, node.name)] = node
            elif isinstance(node, ast.ClassDef):
                classes.setdefault(mod, set()).add(node.name)
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defs[(mod, f"{node.name}.{sub.name}")] = sub
    return defs, imports, classes


def _resolve(defs, imports, classes, mod, name):
    if (mod, name) in defs:
        return [(mod, name)]
    if name in classes.get(mod, ()):
        return [(mod, f"{name}.__init__"), (mod, f"{name}.__post_init__")]
    target = imports.get(mod, {}).get(name)
    if target and ":" in target:
        tmod, tname = target.split(":")
        if (tmod, tname) in defs:
            return [(tmod, tname)]
        if tname in classes.get(tmod, ()):
            return [(tmod, f"{tname}.__init__"), (tmod, f"{tname}.__post_init__")]
    return []


def _call_tree():
    defs, imports, classes = _index()
    seen: set[tuple[str, str]] = set()
    stack = [PREPARE]
    while stack:
        key = stack.pop()
        if key in seen or key not in defs:
            continue
        seen.add(key)
        mod, qual = key
        cls = qual.split(".")[0] if "." in qual else None
        for call in ast.walk(defs[key]):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if isinstance(func, ast.Name):
                stack += _resolve(defs, imports, classes, mod, func.id)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                owner = func.value.id
                if owner in {"self", "cls"} and cls:
                    stack.append((mod, f"{cls}.{func.attr}"))
                    continue
                target = imports.get(mod, {}).get(owner)
                if target and ":" not in target:
                    stack += _resolve(defs, imports, classes, target, func.attr)
                elif target:
                    tmod, tname = target.split(":")
                    if tname in classes.get(tmod, ()):
                        stack.append((tmod, f"{tname}.{func.attr}"))
                    else:
                        stack += _resolve(defs, imports, classes, f"{tmod}.{tname}", func.attr)
                elif owner in classes.get(mod, ()):
                    stack.append((mod, f"{owner}.{func.attr}"))
            for arg in [*call.args, *(kw.value for kw in call.keywords)]:
                if isinstance(arg, ast.Name):
                    stack += _resolve(defs, imports, classes, mod, arg.id)
    return {key: defs[key] for key in seen}


@pytest.fixture(scope="module")
def call_tree():
    tree = _call_tree()
    assert len(tree) > 300, "the prepare call tree must resolve, not silently shrink"
    return tree


def _raised_type(node: ast.Raise) -> str | None:
    exc = node.exc
    if isinstance(exc, ast.Call):
        return ast.unparse(exc.func)
    return None  # bare re-raise or re-raise of a bound name


def test_every_non_value_error_raised_in_family_prepare_is_a_reviewed_cut_type(call_tree):
    unreviewed = sorted(
        f"{mod}:{node.lineno}:{qual}: raise {raised}"
        for (mod, qual), fn in call_tree.items()
        for node in ast.walk(fn)
        if isinstance(node, ast.Raise)
        and (raised := _raised_type(node)) is not None
        and raised not in VALUE_ERROR_SUBCLASSES
        and raised not in CUT_LEVEL_TYPES
    )
    assert not unreviewed, (
        "A family's prepare raises an exception type nobody classified. A family "
        "evidence verdict must be a ValueError (it then excludes only its family); "
        "a shared-state fault must be a GlobalValueFault or a reviewed cut type in "
        "CUT_LEVEL_TYPES:\n" + "\n".join(unreviewed)
    )


def test_db_or_os_fault_converted_to_value_error_keeps_its_cause(call_tree):
    hidden = []
    for (mod, qual), fn in call_tree.items():
        for handler in ast.walk(fn):
            if not isinstance(handler, ast.ExceptHandler) or handler.type is None:
                continue
            caught = (
                {ast.unparse(e) for e in handler.type.elts}
                if isinstance(handler.type, ast.Tuple)
                else {ast.unparse(handler.type)}
            )
            if not caught & DB_OR_OS:
                continue
            for node in ast.walk(handler):
                if (
                    isinstance(node, ast.Raise)
                    and _raised_type(node) in VALUE_ERROR_SUBCLASSES
                    and isinstance(node.cause, ast.Constant)
                    and node.cause.value is None
                ):
                    hidden.append(f"{mod}:{node.lineno}:{qual}")
    assert not hidden, (
        "A DB/OS fault is re-raised as a family verdict with `from None`; chain "
        "it so the rule still sees the DB fault:\n" + "\n".join(hidden)
    )


@pytest.mark.parametrize(
    ("fault", "tag"),
    (
        (ValueError("ANY_NEW_FAMILY_EVIDENCE_REASON"), FAMILY_AUTHORITY_UNAVAILABLE),
        (sqlite3.OperationalError("database is locked"), TRANSIENT_FAMILY_AUTHORITY_UNAVAILABLE),
        (sqlite3.OperationalError("disk I/O error"), None),
        (OSError("no space left on device"), None),
        (GlobalValueFault("GLOBAL_PROBABILITY_USE_INVALID"), None),
        (RuntimeError("boom"), None),
        (TypeError("boom"), None),
    ),
)
def test_family_fault_tag(fault, tag):
    assert family_fault_tag(fault) == tag


def test_wrapped_db_fault_is_cut_level_even_under_from_none():
    try:
        try:
            raise sqlite3.DatabaseError("database disk image is malformed")
        except sqlite3.Error:
            raise ValueError("DAY0_FAMILY_VERDICT") from None
    except ValueError as exc:
        wrapped = exc
    assert family_fault_tag(wrapped) is None


def test_wrapped_lock_stays_transient_family_scope():
    try:
        try:
            raise sqlite3.OperationalError("database is locked")
        except sqlite3.Error as exc:
            raise ValueError("NOAA_PRELIMINARY_SURVIVAL_EVIDENCE_UNAVAILABLE") from exc
    except ValueError as exc:
        wrapped = exc
    assert family_fault_tag(wrapped) == TRANSIENT_FAMILY_AUTHORITY_UNAVAILABLE
