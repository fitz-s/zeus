# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-position-model-refactor.md (Finding 1, PR A scaffold; flipped to passing by PR C0)
"""Antibody invariants: `chain_verified_at` is a POSITIVE-observation timestamp only.

Finding 1 (P1 confirmed bug): chain_reconciliation.py currently writes
`pos.chain_verified_at = now_iso` in branches where the local position is
ABSENT from the chain snapshot (lines 920 — local-only missing; 931 — exit
pending missing). Downstream classifier `classify_chain_state()` then
treats those timestamps as "recent positive verification", inverting the
intended semantics of CHAIN_EMPTY vs CHAIN_UNKNOWN.

The fix (PR C0, same session):
  1. Add `Position.last_chain_absence_observed_at`.
  2. Stop writing `chain_verified_at` on absence; write absence field instead.
  3. Classifier reads positive observations only.

This test is STRICT-XFAIL until PR C0 lands. Meta-verify protocol: PR C0
flips both invariants below to passing without modifying the test body.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CHAIN_RECON_PATH = REPO_ROOT / "src" / "state" / "chain_reconciliation.py"
PORTFOLIO_PATH = REPO_ROOT / "src" / "state" / "portfolio.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PR A scaffold (Finding 1): Position.last_chain_absence_observed_at "
        "does not yet exist; PR C0 (same session) adds it."
    ),
)
def test_position_has_chain_absence_observation_field() -> None:
    """Position dataclass must have a typed absence-observation timestamp distinct
    from `chain_verified_at`."""
    source = _read(PORTFOLIO_PATH)
    assert "last_chain_absence_observed_at" in source, (
        "Position is missing `last_chain_absence_observed_at` — required to split "
        "positive chain observation from absence observation (Finding 1)."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PR A scaffold (Finding 1): chain_reconciliation.py:920 and :931 still write "
        "`chain_verified_at = now` on absence branches. PR C0 switches these to "
        "`last_chain_absence_observed_at`."
    ),
)
def test_chain_reconciliation_absence_branches_do_not_advance_positive_timestamp() -> None:
    """Walk chain_reconciliation.py AST; any assignment of `pos.chain_verified_at`
    inside a function body that ALSO references a known absence-branch marker
    (`local_only`, `exit_pending_missing`, "missing from chain") is a violation.

    Heuristic acknowledged: this catches the documented sites but is intentionally
    conservative. After PR C0 those assignments become writes to
    `last_chain_absence_observed_at` and this test passes.
    """
    source = _read(CHAIN_RECON_PATH)
    tree = ast.parse(source)

    ABSENCE_MARKERS = ("local_only", "exit_pending_missing", "missing from chain", "not in chain")

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_segment = ast.get_source_segment(source, node) or ""
        # Only inspect functions that touch absence branches.
        if not any(marker in body_segment for marker in ABSENCE_MARKERS):
            continue
        # In an absence-touching function, find every `pos.chain_verified_at = ...` write.
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Assign):
                continue
            for target in sub.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "chain_verified_at"
                ):
                    violations.append(
                        f"{CHAIN_RECON_PATH.name}:{sub.lineno}: "
                        f"{node.name}() writes chain_verified_at while body references absence markers"
                    )

    assert not violations, (
        "chain_verified_at must represent POSITIVE chain observation only. "
        "Detected absence-branch writes:\n  " + "\n  ".join(violations)
    )
