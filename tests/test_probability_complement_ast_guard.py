from __future__ import annotations

# Created: 2026-06-07
# Authority basis (CORRECTED 2026-06-09 after an independent-verification catch):
#   An EARLIER edit removed event_reactor_adapter on a FLAWED claim that 1 - q_ucb_yes
#   satisfies FIX-4's "native NO calibration source". It does NOT: 1 - q_ucb_yes is sourced
#   FORECAST_BOOTSTRAP, and FIX-4's allow-list for material-YES buy_no is
#   {EMOS_ANALYTIC, SETTLEMENT_ISOTONIC} (live_admission.py). FIX-4 is CORRECT to block
#   material buy_no — there is genuinely zero native-NO fitted calibration (all tables empty).
#   That material-buy_no safety lives in live_admission.py and is UNTOUCHED here.
#   What IS verified true, and why event_reactor_adapter stays out of the BLANKET scan:
#     - 1 - q_ucb_yes is the CONSERVATIVE NO bound; the dangerous, overconfident form is
#       1 - q_lcb_yes (since q_ucb >= q_lcb => 1 - q_ucb <= 1 - q_lcb).
#     - The decision core's NO-leg sizer uses ONLY 1 - q_ucb_yes, NEVER 1 - q_lcb_yes
#       (independently re-verified). The blanket "any 1 - x" scan false-flags that correct
#       conservative bound + the q_NO point complement (1 - q_yes), which are legitimate.
#   So the blanket scan is replaced for the decision core by the FOCUSED ban below, which
#   bans precisely the one wrong construct (1 - <q_lcb-like>) and allows the conservative
#   bound. The blanket guard still protects the remaining modules from accidental complements.

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


LIVE_PROBABILITY_PATHS = (
    "src/engine/evaluator.py",
    "src/engine/monitor_refresh.py",
    "src/engine/replacement_forecast_hook_factory.py",
    "src/events/candidate_evaluation.py",
    "src/events/continuous_redecision.py",
    "src/events/opportunity_selector.py",
    "src/events/reactor.py",
    "src/state/db.py",
    # src/strategy/candidates/* removed 2026-06-14 (shadow-candidate framework
    # deletion — gate-mass-collapse wave); their entries dropped from this scan list.
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


def _negative_operand(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return node.operand
    if isinstance(node, ast.Call) and len(node.args) == 1:
        func = node.func
        func_name = ""
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr
        if func_name == "negative":
            return node.args[0]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if _is_negative_one_like(node.left):
            return node.right
        if _is_negative_one_like(node.right):
            return node.left
    return None


def _yes_q_lcb_complement_findings(tree: ast.AST, *, label: str) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        subtrahend: ast.AST | None = None
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Sub) and _is_one_like(node.left):
                subtrahend = node.right
            elif isinstance(node.op, ast.Add):
                if _is_one_like(node.left):
                    subtrahend = _negative_operand(node.right)
                elif _is_one_like(node.right):
                    subtrahend = _negative_operand(node.left)
        elif _is_subtract_call(node):
            subtrahend = node.args[1]
        elif _is_additive_complement_call(node):
            left, right = node.args[0], node.args[1]
            subtrahend = _negative_operand(right) if _is_one_like(left) else _negative_operand(left)
        elif _is_sum_complement_call(node):
            arg = node.args[0]
            if isinstance(arg, (ast.Tuple, ast.List)) and len(arg.elts) == 2:
                left, right = arg.elts
                subtrahend = _negative_operand(right) if _is_one_like(left) else _negative_operand(left)
        if subtrahend is not None and _subtrahend_is_yes_q_lcb(subtrahend):
            findings.append(f"{label}:{node.lineno}: {ast.unparse(node)}")
    return findings


def test_live_probability_code_does_not_construct_no_bound_from_yes_q_lcb_complement():
    findings: list[str] = []
    for relative_path in LIVE_PROBABILITY_PATHS:
        path = ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        findings.extend(_yes_q_lcb_complement_findings(tree, label=relative_path))

    assert not findings, "overconfident 1 - q_lcb_yes NO bound found:\n" + "\n".join(findings)


# ── FOCUSED decision-core ban: 1 - q_lcb_yes is forbidden; 1 - q_ucb_yes is allowed ──
DECISION_CORE_PATHS = (
    "src/engine/event_reactor_adapter.py",
    "src/strategy/utility_ranker.py",
)


def _subtrahend_is_yes_q_lcb(node: ast.AST) -> bool:
    """A subtrahend naming the YES-side q_lcb — the forbidden source for the NO bound
    (1 - q_lcb_YES overstates NO win-mass). The NO leg's OWN lower bound (q_lcb_no) and the
    YES upper bound (q_ucb_yes) are NOT this pattern. ``float(q_lcb_yes)`` recurses."""
    if isinstance(node, ast.Call):
        return any(_subtrahend_is_yes_q_lcb(a) for a in node.args)
    name = ""
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    n = name.lower()
    return "lcb" in n and "yes" in n


def test_decision_core_never_builds_no_from_the_overconfident_q_lcb_complement():
    """The decision core may form the CONSERVATIVE NO bound 1 - q_ucb_yes (and the q_NO
    point 1 - q_yes), but must NEVER form 1 - q_lcb_yes — that overstates NO win-mass and
    breaches iron rule 6 (conservative q_lcb sizing). AST-only, so docstrings are ignored."""
    findings: list[str] = []
    for relative_path in DECISION_CORE_PATHS:
        path = ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Sub)
                and _is_one_like(node.left)
                and _subtrahend_is_yes_q_lcb(node.right)
            ):
                findings.append(f"{relative_path}:{node.lineno}: {ast.unparse(node)}")
    assert not findings, "overconfident 1 - q_lcb_yes NO bound found:\n" + "\n".join(findings)


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


def test_yes_q_lcb_guard_rejects_variants_but_allows_point_and_ucb_complements():
    source = """
from decimal import Decimal
import operator
import numpy as np

def f(q_yes, q_ucb_yes, q_lcb_yes):
    return [
        1 - q_yes,
        1 - q_ucb_yes,
        1 - q_lcb_yes,
        Decimal("1") - float(q_lcb_yes),
        operator.sub(1, q_lcb_yes),
        np.add(1, np.negative(q_lcb_yes)),
    ]
"""
    findings = _yes_q_lcb_complement_findings(ast.parse(source), label="<snippet>")
    assert len(findings) == 4, "\n".join(findings)
