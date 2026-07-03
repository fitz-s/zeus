# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""validate_family_decision_contract + sentinel facts-writer (consult REV-2 blocker 5).

The ON-mode soft-fail hazard: _record_qkernel_selection_family_facts reads FamilyDecision via
getattr-with-default, so a missing/renamed/nulled field degrades attribution SILENTLY. The
validator must catch that loudly; the sentinel facts-writer proves no getattr default fires for
a valid decision and fires (is caught) for a broken one.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.solve.solver import (
    _REQUIRED_FAMILY_DECISION_FIELDS,
    FamilyDecisionContractError,
    validate_family_decision_contract,
)

_SENTINEL = object()


def _good_decision(**over):
    fields = dict(
        decision_id="tokyo@2026-07-03",
        case=SimpleNamespace(),
        predictive=SimpleNamespace(),
        omega=SimpleNamespace(bins=()),
        joint_q=SimpleNamespace(),
        band=SimpleNamespace(),
        family_book=SimpleNamespace(),
        market_coherence=SimpleNamespace(),
        candidates=(),
        selected=SimpleNamespace(),      # a trade
        no_trade_reason=None,
        receipt_hash="deadbeef",
        candidate_decisions=(SimpleNamespace(),),
        market_implied_q=SimpleNamespace(),
        portfolio_comparisons=(),
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def _facts_writer_reads(decision) -> list[str]:
    """Mirror the getattr-with-default consumer; return fields that fell back to the default."""
    fired = []
    for f in _REQUIRED_FAMILY_DECISION_FIELDS:
        if getattr(decision, f, _SENTINEL) is _SENTINEL:
            fired.append(f)
    return fired


def test_valid_trade_decision_passes_and_no_default_fires():
    d = _good_decision()
    assert validate_family_decision_contract(d) is d
    assert _facts_writer_reads(d) == []  # sentinel: no getattr default fired


def test_valid_no_trade_decision_passes():
    d = _good_decision(selected=None, no_trade_reason="NO_EDGE")
    assert validate_family_decision_contract(d) is d


def test_missing_field_caught_before_facts_writer_degrades():
    d = _good_decision()
    delattr(d, "candidate_decisions")
    # the sentinel facts-writer WOULD silently degrade (default fires)...
    assert "candidate_decisions" in _facts_writer_reads(d)
    # ...but the validator catches it loudly first.
    with pytest.raises(FamilyDecisionContractError, match="candidate_decisions|missing"):
        validate_family_decision_contract(d)


def test_nulled_id_rejected():
    with pytest.raises(FamilyDecisionContractError, match="decision_id"):
        validate_family_decision_contract(_good_decision(decision_id=""))
    with pytest.raises(FamilyDecisionContractError, match="receipt_hash"):
        validate_family_decision_contract(_good_decision(receipt_hash=None))


def test_candidate_decisions_must_be_tuple():
    with pytest.raises(FamilyDecisionContractError, match="tuple"):
        validate_family_decision_contract(_good_decision(candidate_decisions=None))


def _decision_fields_read_by(func_node, var: str = "decision") -> set:
    """Every ``getattr(<var>, "X")`` and ``<var>.X`` field read inside a function AST node."""
    import ast

    fields: set = set()
    for node in ast.walk(func_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == var
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            fields.add(node.args[1].value)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == var:
            fields.add(node.attr)
    return fields


def _func_node(path: str, name: str):
    import ast

    with open(path) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_ast_sentinel_real_facts_writer_fields_are_covered():
    # Tie the sentinel to the REAL consumer (consult REV-2 follow-up MEDIUM): statically extract
    # every FamilyDecision field the live facts writer reads and assert the validator's required
    # list is a SUPERSET. A new getattr(decision, "…") consumer that the validator doesn't cover
    # breaks this test — it cannot silently degrade attribution.
    node = _func_node("src/engine/event_reactor_adapter.py", "_record_qkernel_selection_family_facts")
    assert node is not None, "facts writer function not found — the seam moved; re-point the sentinel"
    read = _decision_fields_read_by(node, "decision")
    assert read, "expected the facts writer to read FamilyDecision fields"
    uncovered = read - set(_REQUIRED_FAMILY_DECISION_FIELDS)
    assert not uncovered, (
        f"facts writer reads FamilyDecision fields not in the validator's required list: {uncovered} "
        "— add them to _REQUIRED_FAMILY_DECISION_FIELDS or the validator will not guard them"
    )


def test_ast_sentinel_detects_a_new_uncovered_consumer():
    # Prove the mechanism bites: a synthetic consumer that reads an un-required field is flagged.
    import ast

    src = "def consumer(decision):\n    return getattr(decision, 'brand_new_unguarded_field', None)\n"
    node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))
    read = _decision_fields_read_by(node, "decision")
    assert read == {"brand_new_unguarded_field"}
    assert read - set(_REQUIRED_FAMILY_DECISION_FIELDS), "sentinel must flag an un-required field"


def test_selected_xor_no_trade_reason_enforced():
    # both None -> invalid
    with pytest.raises(FamilyDecisionContractError, match="exactly one"):
        validate_family_decision_contract(_good_decision(selected=None, no_trade_reason=None))
    # both set -> invalid
    with pytest.raises(FamilyDecisionContractError, match="exactly one"):
        validate_family_decision_contract(_good_decision(selected=SimpleNamespace(), no_trade_reason="x"))
