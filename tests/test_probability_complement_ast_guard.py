from __future__ import annotations

# Created: 2026-06-07
# Authority basis: live-money bug audit — Polymarket YES/NO legs are independent
# executable assets; production code must not construct one side with ``1 - x``.

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


LIVE_PROBABILITY_PATHS = (
    "src/engine/event_reactor_adapter.py",
    "src/engine/evaluator.py",
    "src/engine/monitor_refresh.py",
    "src/engine/replacement_forecast_hook_factory.py",
    "src/events/candidate_evaluation.py",
    "src/events/continuous_redecision.py",
    "src/events/opportunity_selector.py",
    "src/events/reactor.py",
    "src/state/db.py",
    "src/strategy/candidates/center_sell_model_no.py",
    "src/strategy/candidates/imminent_open_capture_posterior_collapse.py",
    "src/strategy/candidates/opening_inertia_relaxation.py",
    "src/strategy/candidates/settlement_capture_shadow.py",
    "src/strategy/exit_family_optimizer.py",
    "src/strategy/live_inference/live_admission.py",
    "src/strategy/market_analysis.py",
    "src/strategy/market_analysis_family_scan.py",
)


_ONE_NAMES = {
    "one",
    "ONE",
    "unit",
    "UNIT",
    "unit_probability",
    "UNIT_PROBABILITY",
    "probability_one",
    "PROBABILITY_ONE",
}


def _is_decimal_one_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = ""
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name != "Decimal" or len(node.args) != 1:
        return False
    arg = node.args[0]
    return isinstance(arg, ast.Constant) and arg.value in (1, 1.0, "1", "1.0")


def _is_one_like(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _is_one_like(node.operand)
    if isinstance(node, ast.Constant):
        return node.value is True or node.value in (1, 1.0, "1", "1.0")
    if isinstance(node, ast.Name):
        return node.id in _ONE_NAMES
    return _is_decimal_one_call(node)


def _is_unary_negative_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return True
    if isinstance(node, ast.Call) and len(node.args) == 1:
        func = node.func
        func_name = ""
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr
        if func_name == "negative":
            return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return (
            _is_negative_one_like(node.left)
            or _is_negative_one_like(node.right)
        )
    return False


def _is_negative_one_like(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and _is_one_like(node.operand)
    )


def _is_subtract_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or len(node.args) < 2:
        return False
    func = node.func
    if isinstance(func, ast.Name):
        func_name = func.id
    elif isinstance(func, ast.Attribute):
        func_name = func.attr
    else:
        return False
    return func_name in {"sub", "subtract"} and _is_one_like(node.args[0])


def _is_additive_complement_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or len(node.args) < 2:
        return False
    func = node.func
    if isinstance(func, ast.Name):
        func_name = func.id
    elif isinstance(func, ast.Attribute):
        func_name = func.attr
    else:
        return False
    if func_name not in {"add"}:
        return False
    left, right = node.args[0], node.args[1]
    return (
        _is_one_like(left)
        and _is_unary_negative_expression(right)
    ) or (
        _is_unary_negative_expression(left)
        and _is_one_like(right)
    )


def _is_sum_complement_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or len(node.args) != 1:
        return False
    func = node.func
    if isinstance(func, ast.Name):
        func_name = func.id
    elif isinstance(func, ast.Attribute):
        func_name = func.attr
    else:
        return False
    if func_name not in {"sum", "fsum"}:
        return False
    arg = node.args[0]
    if not isinstance(arg, (ast.Tuple, ast.List)) or len(arg.elts) != 2:
        return False
    left, right = arg.elts
    return (
        _is_one_like(left)
        and _is_unary_negative_expression(right)
    ) or (
        _is_unary_negative_expression(left)
        and _is_one_like(right)
    )


def _one_minus_findings(tree: ast.AST, *, label: str) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Sub) and _is_one_like(node.left):
                findings.append(f"{label}:{node.lineno}: {ast.unparse(node)}")
            elif (
                isinstance(node.op, ast.Add)
                and (
                    (
                        _is_one_like(node.left)
                        and _is_unary_negative_expression(node.right)
                    )
                    or (
                        _is_unary_negative_expression(node.left)
                        and _is_one_like(node.right)
                    )
                )
            ):
                findings.append(f"{label}:{node.lineno}: {ast.unparse(node)}")
            continue
        if _is_subtract_call(node):
            findings.append(f"{label}:{node.lineno}: {ast.unparse(node)}")
            continue
        if _is_additive_complement_call(node):
            findings.append(f"{label}:{node.lineno}: {ast.unparse(node)}")
            continue
        if _is_sum_complement_call(node):
            findings.append(f"{label}:{node.lineno}: {ast.unparse(node)}")
    return findings


def _one_minus_expressions(relative_path: str) -> list[str]:
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _one_minus_findings(tree, label=relative_path)


def _one_minus_snippet_findings(source: str) -> list[str]:
    return _one_minus_findings(ast.parse(source), label="<snippet>")


def test_live_probability_code_does_not_construct_complements_with_one_minus_x():
    findings: list[str] = []
    for relative_path in LIVE_PROBABILITY_PATHS:
        findings.extend(_one_minus_expressions(relative_path))

    assert not findings, "\n".join(findings)


def test_guard_rejects_one_minus_variants_not_just_literal_int_one_minus_x():
    source = """
from decimal import Decimal
import operator
import numpy as np

def f(x):
    one = 1
    ONE = 1.0
    probability_one = Decimal("1")
    return [
        1 - x,
        1.0 - x,
        True - x,
        "1" - x,
        Decimal("1") - x,
        Decimal(1) - x,
        one - x,
        ONE - x,
        probability_one - x,
        1 + -x,
        operator.sub(1, x),
        np.subtract(1.0, x),
        sum((1.0, -x)),
        math.fsum([1.0, -x]),
        sum((-x, 1.0)),
        -x + 1,
        1 + (-1 * x),
        1 + (x * -1),
        operator.add(1, -x),
        np.add(1, np.negative(x)),
    ]
"""
    findings = _one_minus_snippet_findings(source)
    assert len(findings) == 20, "\n".join(findings)
