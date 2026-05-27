# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-position-model-refactor.md (Finding 3, PR A — invariant scaffold)
"""Antibody invariants: no fake `Position` reaches the trading path.

Finding 3 (P1 complexity debt): chain-only venue tokens are currently
materialized as fake `Position` objects with sentinel trade_id prefix
`CHAIN_ONLY_QUARANTINE_*`. They should be `ChainOnlyFact` review-queue
entries, not entries in `PortfolioState.positions`.

This test is STRICT-XFAIL until PR C eliminates the fake `Position`
construction at:
  - src/state/chain_reconciliation.py:1052
  - src/state/portfolio.py._chain_only_quarantine_position_from_row

Meta-verify protocol: when PR C lands, this test must FLIP to passing
without modification.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Two synthetic-Position identity schemes exist on main (Finding 3 confirms incompatibility):
#   - src/state/chain_reconciliation.py:1055     trade_id=QUARANTINE_SENTINEL  (singleton "QUARANTINE_UNRESOLVED")
#   - src/state/portfolio.py:1400 _chain_only_quarantine_position_from_row
#                                                trade_id=f"quarantine_{token_id[:8]}"
# Both share two unmistakable markers: direction="unknown" AND chain_state="quarantined".
# In healthy trading paths Direction is buy_yes/buy_no, so a literal direction="unknown"
# argument to Position(...) is the cleanest static signature of a synthetic chain-only stub.

FORBIDDEN_PRODUCER_FILES = (
    "src/state/chain_reconciliation.py",
    "src/state/portfolio.py",
)


def _calls_position_with_unknown_direction(source: str) -> list[int]:
    """Return linenos for every Position(...) call where `direction` is the literal "unknown"."""
    tree = ast.parse(source)
    hits: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_position_call = (
            (isinstance(func, ast.Name) and func.id == "Position")
            or (isinstance(func, ast.Attribute) and func.attr == "Position")
        )
        if not is_position_call:
            continue
        for kw in node.keywords:
            if kw.arg != "direction":
                continue
            value = kw.value
            if isinstance(value, ast.Constant) and value.value == "unknown":
                hits.append(node.lineno)
    return hits


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PR A scaffold (Finding 3): chain_reconciliation.py:~1055 and "
        "portfolio._chain_only_quarantine_position_from_row still construct fake "
        'Position objects with direction="unknown" (chain-only stub marker). '
        "PR C replaces them with typed ChainOnlyFact entries."
    ),
)
def test_no_position_constructed_with_unknown_direction() -> None:
    violations: list[str] = []
    for rel_path in FORBIDDEN_PRODUCER_FILES:
        path = REPO_ROOT / rel_path
        source = path.read_text(encoding="utf-8")
        for lineno in _calls_position_with_unknown_direction(source):
            violations.append(f'{rel_path}:{lineno}: Position(direction="unknown", ...)')

    assert not violations, (
        'Synthetic Position(...) constructors with direction="unknown" detected. '
        "Chain-only tokens must be ChainOnlyFact review entries, not Position. "
        "Producers found:\n  " + "\n  ".join(violations)
    )
